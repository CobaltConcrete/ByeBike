import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from app import create_app
from byebike.cloud import CloudUploader
from byebike.storage import EvidenceStore, read_json, write_json


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"BYEBIKE_GCS_ENABLED":"false"})
        self.env.start()
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["camera"].close()
        self.app.extensions["cloud"].close()
        self.app.extensions["cloud"].thread.join(timeout=2)
        self.env.stop()
        self.directory.cleanup()

    def test_monitor_requires_camera_and_pedestrian_zone(self):
        self.assertEqual(self.client.post('/api/mode',json={"mode":"monitor"}).status_code,400)
        self.assertEqual(self.client.get('/api/frame.jpg').status_code,503)

    def test_invalid_settings_and_cross_origin_requests(self):
        self.assertEqual(self.client.post('/api/camera',json={"kind":"usb","source":"not-a-number"}).status_code,400)
        self.assertEqual(self.client.post('/api/rules',json={"overlap":1.1}).status_code,400)
        self.assertEqual(self.client.post('/api/cloud',json={"enabled":True,"bucket":""}).status_code,400)
        self.assertEqual(self.client.post('/api/disconnect',json={},headers={"Origin":"https://unrelated.example"}).status_code,403)

    def test_sample_camera_paths_and_settings_persist(self):
        self.assertEqual(self.client.post('/api/camera',json={"kind":"demo","label":"Test sample"}).status_code,200)
        deadline = time.monotonic()+5
        while not self.app.extensions["camera"].state()["has_frame"] and time.monotonic()<deadline:
            time.sleep(.05)
        state = self.client.get('/api/state').get_json()
        self.assertTrue(state["has_frame"])
        self.assertEqual(self.client.post('/api/mode',json={"mode":"monitor"}).status_code,400)
        zone = {"kind":"pedestrian","points":[[0,.75],[1,.75],[1,1],[0,1]]}
        self.assertEqual(self.client.post('/api/zones',json={"source_key":"wrong-camera","zones":[zone]}).status_code,400)
        self.assertEqual(self.client.post('/api/zones',json={"source_key":state["source_key"],"zones":[zone]}).status_code,200)
        stored = read_json(Path(self.directory.name)/'paths.json',{})
        self.assertEqual(stored[state["source_key"]]["zones"][0]["kind"],"pedestrian")
        image = self.client.get('/api/frame.jpg?raw=1')
        self.assertEqual(image.mimetype,'image/jpeg')
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(image.data,np.uint8),cv2.IMREAD_COLOR))


class EvidenceTests(unittest.TestCase):
    def test_env_cloud_settings_override_saved_config_without_requiring_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "BYEBIKE_GCS_ENABLED":"true", "BYEBIKE_GCS_BUCKET":"gs://byebike-env-test",
            "BYEBIKE_GCS_PROJECT":"demo-project", "BYEBIKE_GCS_PREFIX":"/crops/",
            "GOOGLE_APPLICATION_CREDENTIALS":"",
        }):
            root = Path(directory)
            write_json(root/'cloud.json', {"enabled":False, "bucket":"old-bucket"})
            uploader = CloudUploader(EvidenceStore(root/'evidence'),root/'cloud.json')
            try:
                config = uploader.state()['config']
                self.assertTrue(config['enabled'])
                self.assertEqual(config['bucket'],'byebike-env-test')
                self.assertEqual(config['project'],'demo-project')
                self.assertEqual(config['prefix'],'crops')
                self.assertEqual(config['credentials_file'],'')
            finally:
                uploader.close()
                uploader.thread.join(timeout=2)

    def test_incomplete_env_disables_cloud_without_breaking_local_app(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "BYEBIKE_GCS_ENABLED":"true", "BYEBIKE_GCS_BUCKET":"invalid/bucket",
        }):
            root = Path(directory)
            uploader = CloudUploader(EvidenceStore(root/'evidence'),root/'cloud.json')
            try:
                self.assertFalse(uploader.state()['config']['enabled'])
                self.assertIn('disabled',uploader.state()['status'])
            finally:
                uploader.close()
                uploader.thread.join(timeout=2)

    def test_crop_contains_rider_and_bike_and_metadata_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(directory)
            frame = np.full((100,100,3),160,dtype=np.uint8)
            cyclist = {"box":[20,5,90,90],"bike_box":[20,50,90,90],"person_box":[30,5,60,85],"track_id":1,"confidence":.9,"overlap":.5,"event_type":"crossed_from_cycling"}
            event = store.save(frame,frame,cyclist,{"label":"Test","kind":"demo"},[],{"overlap":.25})
            crop = cv2.imread(str(Path(directory)/event['id']/'crop.jpg'))
            self.assertEqual(crop.shape[:2],(100,92))
            restarted,count = EvidenceStore(directory).list()
            self.assertEqual(count,1)
            self.assertEqual(restarted[0]['thresholds']['overlap'],.25)

    def test_cloud_failure_retains_crop_and_can_retry_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{"BYEBIKE_GCS_ENABLED":"false"}):
            root = Path(directory)
            store = EvidenceStore(root/'evidence')
            uploader = CloudUploader(store,root/'cloud.json')
            event = {"id":"test-event","timestamp":"2026-10-16T10:00:00+00:00","event_type":"crossed_from_cycling","cloud":{"status":"local"}}
            (store.root/event['id']).mkdir()
            (store.root/event['id']/'crop.jpg').write_bytes(b'crop-data')
            write_json(store.root/event['id']/'event.json',event)
            fake = MagicMock()
            upload = fake.bucket.return_value.blob.return_value.upload_from_filename
            upload.side_effect = OSError('offline')
            try:
                with patch.object(uploader,'_client',return_value=fake):
                    uploader.configure({"enabled":True,"bucket":"byebike-test-bucket"})
                    uploader.enqueue(event)
                    path = store.root/event['id']/'event.json'
                    self.wait_status(path,'error')
                    self.assertTrue((path.parent/'crop.jpg').exists())
                    upload.side_effect = None
                    uploader.retry()
                    self.wait_status(path,'uploaded')
                    self.assertEqual(upload.call_args.kwargs['if_generation_match'],0)
                    self.assertEqual(read_json(path,{})['cloud']['uri'],'gs://byebike-test-bucket/byebike/2026-10-16/test-event.jpg')
            finally:
                uploader.close()
                uploader.thread.join(timeout=2)

    def wait_status(self,path,status):
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            if read_json(path,{}).get('cloud',{}).get('status') == status:
                return
            time.sleep(.03)
        self.fail(f'Expected upload status {status}, got {read_json(path,{})}')


if __name__ == '__main__':
    unittest.main()
