import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
from google.api_core.exceptions import Forbidden, NotFound
import numpy as np

from app import create_app
from byebike.hosted import GcsEvidenceStore, GcsStore


class MemoryBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def download_as_bytes(self, **kwargs):
        if self.name not in self.bucket.objects:
            raise NotFound("Missing")
        return self.bucket.objects[self.name]

    def download_as_text(self, **kwargs):
        return self.download_as_bytes().decode()

    def upload_from_string(self, value, **kwargs):
        if self.bucket.fail_writes:
            raise Forbidden("Storage denied")
        self.bucket.objects[self.name] = value.encode()

    def upload_from_filename(self, filename, **kwargs):
        if self.bucket.fail_writes:
            raise Forbidden("Storage denied")
        self.bucket.objects[self.name] = Path(filename).read_bytes()


class MemoryBucket:
    name = "byebike-unit-test"

    def __init__(self):
        self.objects, self.fail_writes = {}, False

    def blob(self, name):
        return MemoryBlob(self, name)


class MemoryClient:
    def __init__(self):
        self.memory = MemoryBucket()

    def bucket(self, name):
        return self.memory

    def list_blobs(self, bucket, prefix, **kwargs):
        return [bucket.blob(name) for name in bucket.objects if name.startswith(prefix)]


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = MemoryClient()
        self.password = "a-test-password-with-at-least-24-characters"
        self.env = patch.dict(os.environ, {"BYEBIKE_HOSTED":"true", "BYEBIKE_APP_PASSWORD":self.password,
            "BYEBIKE_GCS_BUCKET":"byebike-unit-test", "BYEBIKE_GCS_PROJECT":"unit-test", "BYEBIKE_GCS_PREFIX":"test", "K_SERVICE":""})
        self.env.start()
        self.storage = patch("byebike.hosted.storage.Client", return_value=self.memory)
        self.storage.start()
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["camera"].close()
        self.storage.stop()
        self.env.stop()
        self.temp.cleanup()

    def login(self):
        self.assertEqual(self.client.post("/api/login", json={"password":self.password}).status_code, 200)

    def test_password_gate_covers_api_and_evidence_and_checks_origin(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.assertEqual(self.client.get("/evidence/anything/crop.jpg").status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"password":"wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"password":self.password}, headers={"Origin":"https://other.example"}).status_code, 403)
        self.login()
        self.assertEqual(self.client.get("/api/state").status_code, 200)
        self.assertEqual(self.client.post("/api/cloud", json={}).status_code, 400)
        self.client.post("/api/logout", json={})
        self.assertEqual(self.client.get("/api/state").status_code, 401)

    def test_browser_capture_tokens_disconnect_and_stale_frames(self):
        self.login()
        connected = self.client.post("/api/camera", json={"kind":"browser","source":"device-one"}).get_json()
        self.assertNotIn("capture_token", self.client.get("/api/state").get_json())
        frame = cv2.imencode(".jpg", np.zeros((360,640,3), dtype=np.uint8))[1].tobytes()
        endpoint = "/api/camera/frame"
        self.assertEqual(self.client.post(endpoint, data=frame, content_type="image/jpeg").status_code, 400)
        headers = {"X-Camera-Token":connected["capture_token"]}
        self.assertEqual(self.client.post(endpoint, data=b"invalid", content_type="image/jpeg", headers=headers).status_code, 400)
        self.assertEqual(self.client.post(endpoint, data=frame, content_type="image/jpeg", headers=headers).status_code, 200)
        camera = self.app.extensions["camera"]
        self.assertEqual(camera.state()["frame_size"], [640,360])
        camera.last_browser_frame = time.monotonic() - 11
        deadline = time.monotonic() + 2
        while camera.state()["status"] != "error" and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(camera.state()["mode"], "setup")
        self.assertEqual(camera.state()["status"], "error")
        self.assertEqual(self.client.post(endpoint, data=frame, content_type="image/jpeg", headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/camera", json={"kind":"usb"}).status_code, 400)

    def test_cloud_paths_survive_new_instance_and_failed_save_is_not_acknowledged(self):
        self.login()
        self.client.post("/api/camera", json={"kind":"demo"})
        camera = self.app.extensions["camera"]
        deadline = time.monotonic() + 3
        while not camera.state()["has_frame"] and time.monotonic() < deadline:
            time.sleep(.02)
        zones = [{"kind":"pedestrian", "points":[[0,.8],[1,.8],[1,1],[0,1]]}]
        zones = camera.save_zones(zones, camera.source_key)["zones"]
        store = GcsStore("byebike-unit-test", "test", "unit-test", client=self.memory)
        self.assertEqual(store.read("paths.json", {})[camera.source_key]["zones"], zones)
        self.memory.memory.fail_writes = True
        with self.assertRaises(Forbidden):
            camera.save_zones([], camera.source_key)
        self.assertEqual(camera.state()["zones"], zones)
        self.memory.memory.fail_writes = False
        restarted = create_app(self.temp.name)
        try:
            self.assertEqual(restarted.extensions["camera"].state()["zones"], zones)
            self.assertEqual(restarted.extensions["camera"].state()["mode"], "setup")
        finally:
            restarted.extensions["camera"].close()

    def test_crop_and_metadata_survive_restart_without_full_scene_or_local_queue(self):
        store = self.app.extensions["evidence"]
        frame = np.zeros((100,100,3), dtype=np.uint8)
        cyclist = {"box":[20,5,90,90],"bike_box":[20,50,90,90],"person_box":[30,5,60,85],"track_id":1,"confidence":.9,"overlap":.5,"event_type":"crossed_from_cycling"}
        event = store.save(frame, frame, cyclist, {"label":"Test", "kind":"browser"}, [], {"overlap":.25})
        self.assertEqual(event["cloud"]["status"], "uploaded")
        self.assertIsNone(event["frame_url"])
        self.assertEqual(list(store.root.iterdir()), [])
        self.assertEqual(len(self.memory.memory.objects), 2)
        restarted = GcsEvidenceStore(store.root, store.store)
        self.assertEqual(restarted.list()[1], 1)
        crop, mime = restarted.read_file(event["id"], "crop.jpg")
        self.assertEqual(mime, "image/jpeg")
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(crop,np.uint8), cv2.IMREAD_COLOR))
        self.assertEqual(json.loads(restarted.read_file(event["id"], "event.json")[0])["id"], event["id"])
        self.assertIsNone(restarted.read_file(event["id"], "frame.jpg"))
        self.memory.memory.fail_writes = True
        with self.assertRaises(Forbidden):
            store.save(frame, frame, cyclist, {"label":"Test", "kind":"browser"}, [], {})
        self.assertEqual(store.list()[1], 1)
        self.assertEqual(list(store.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
