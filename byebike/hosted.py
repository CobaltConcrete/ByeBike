"""Durable Cloud Run storage. The bucket stays private; images pass through the app."""

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import RLock

from google.api_core.exceptions import NotFound
from google.api_core.retry import Retry
from google.cloud import storage

from .storage import EvidenceStore

RETRY = Retry(deadline=20)
EVENT_ID = re.compile(r"^\d{8}T\d{6}_\d{6}Z_[a-f0-9]{8}$")


class GcsStore:
    def __init__(self, bucket, prefix, project, client=None):
        self.client = client or storage.Client(project=project)
        self.bucket = self.client.bucket(bucket)
        self.prefix = prefix.strip("/")
        self.project = project

    def name(self, suffix):
        return "/".join(part for part in (self.prefix, suffix) if part)

    def read(self, filename, default):
        try:
            return json.loads(self.bucket.blob(self.name("cloudrun/" + filename)).download_as_text(timeout=10, retry=RETRY))
        except NotFound:
            return deepcopy(default)

    def write(self, filename, value):
        self.bucket.blob(self.name("cloudrun/" + filename)).upload_from_string(
            json.dumps(value, allow_nan=False), content_type="application/json", timeout=10, retry=RETRY)


class GcsEvidenceStore:
    def __init__(self, root, store):
        self.root, self.store = Path(root), store
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.events = {}
        # Load durable metadata on every container start. No ephemeral gallery index.
        for blob in store.client.list_blobs(store.bucket, prefix=store.name("cloudrun/events/"), timeout=10, retry=RETRY):
            if blob.name.endswith(".json"):
                event = json.loads(blob.download_as_text(timeout=10, retry=RETRY))
                self.events[event["id"]] = event

    def save(self, *args):
        # Bound ephemeral disk usage to the event being saved; no persistent local queue.
        with TemporaryDirectory(dir=self.root) as temporary:
            local = EvidenceStore(temporary)
            event = local.save(*args)
            object_name = self.store.name(f"{event['timestamp'][:10]}/{event['id']}.jpg")
            blob = self.store.bucket.blob(object_name)
            blob.metadata = {"event_id": event["id"], "timestamp": event["timestamp"], "event_type": event["event_type"]}
            blob.upload_from_filename(str(local.root / event["id"] / "crop.jpg"),
                                      content_type="image/jpeg", if_generation_match=0, timeout=10, retry=RETRY)
            event["cloud"] = {"status": "uploaded", "bucket": self.store.bucket.name, "object": object_name,
                              "uri": f"gs://{self.store.bucket.name}/{object_name}"}
            # Only the cyclist crop is persisted, not the full scene.
            event["frame_url"] = None
            self.store.write(f"events/{event['id']}.json", event)
            with self.lock:
                self.events[event["id"]] = event
            return event

    def list(self, limit=60):
        with self.lock:
            return deepcopy([self.events[key] for key in sorted(self.events, reverse=True)[:limit]]), len(self.events)

    def read_file(self, event_id, filename):
        if not EVENT_ID.fullmatch(event_id):
            return None
        with self.lock:
            event = deepcopy(self.events.get(event_id))
        if not event:
            return None
        if filename == "event.json":
            return json.dumps(event, indent=2).encode(), "application/json"
        if filename == "crop.jpg":
            return self.store.bucket.blob(event["cloud"]["object"]).download_as_bytes(timeout=10, retry=RETRY), "image/jpeg"
        return None


class HostedCloud:
    """Same UI interface as the optional local uploader, with deployment-owned config."""
    def __init__(self, store):
        self.store = store

    def state(self):
        return {"managed": True, "config": {"enabled": True, "bucket": self.store.bucket.name,
                "prefix": self.store.prefix, "project": self.store.project, "credentials_file": ""},
                "status": "Crops, event metadata and saved paths are stored in your private GCS bucket."}

    def configure(self, config):
        raise ValueError("Cloud Run storage is managed by the deployment settings.")

    def enqueue(self, event):
        pass  # save() already acknowledged the durable upload.

    def retry(self):
        raise ValueError("Cloud Run saves directly to GCS. After a storage error, fix access and restart monitoring.")

    def close(self):
        pass
