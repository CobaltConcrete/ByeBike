"""Optional GCS crop uploads, retried from the durable local evidence folder."""

from copy import deepcopy
import os
import re
from threading import Event, RLock, Thread
import time

from .storage import read_json, write_json


DEFAULT_CLOUD = {"enabled": False, "bucket": "", "prefix": "byebike", "project": "", "credentials_file": ""}


def validate_cloud(value):
    result = {**DEFAULT_CLOUD, **{key: value[key] for key in DEFAULT_CLOUD if key in value}}
    if not isinstance(result["enabled"], bool):
        raise ValueError("Cloud enabled must be true or false.")
    for key in ("bucket", "prefix", "project", "credentials_file"):
        if not isinstance(result[key], str) or len(result[key]) > 500:
            raise ValueError("Invalid cloud setting.")
        result[key] = result[key].strip()
    result["bucket"] = result["bucket"].removeprefix("gs://").rstrip("/")
    result["prefix"] = result["prefix"].strip("/")
    if result["enabled"] and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", result["bucket"]):
        raise ValueError("Enter a valid GCS bucket name (without a folder path).")
    return result


class CloudUploader:
    def __init__(self, evidence, config_path):
        self.evidence = evidence
        self.config_path = config_path
        self.config = {**DEFAULT_CLOUD, **read_json(config_path, {})}
        for key, variable in (("bucket", "BYEBIKE_GCS_BUCKET"), ("project", "BYEBIKE_GCS_PROJECT"), ("prefix", "BYEBIKE_GCS_PREFIX"), ("credentials_file", "GOOGLE_APPLICATION_CREDENTIALS")):
            if os.environ.get(variable):
                self.config[key] = os.environ[variable]
        if os.environ.get("BYEBIKE_GCS_ENABLED"):
            self.config["enabled"] = os.environ["BYEBIKE_GCS_ENABLED"].lower() in ("1", "true", "yes")
        config_error = None
        try:
            self.config = validate_cloud(self.config)
        except ValueError as error:
            self.config["enabled"] = False
            config_error = str(error)
        self.lock = RLock()
        self.wake = Event()
        self.stop = Event()
        self.status = "Ready to upload new crops." if self.config["enabled"] else "Cloud storage is off. Evidence is saved locally."
        if config_error:
            self.status = f"Cloud uploads disabled: {config_error} Fix .env or the cloud settings."
        self.thread = Thread(target=self._run, daemon=True, name="gcs-uploads")
        self.thread.start()

    def state(self):
        with self.lock:
            return {"config": deepcopy(self.config), "status": self.status}

    def configure(self, config):
        config = validate_cloud(config)
        with self.lock:
            write_json(self.config_path, config)
            self.config = config
            self.status = "Ready to upload new crops." if config["enabled"] else "Cloud storage is off. Evidence is saved locally."
        self.wake.set()
        return self.state()

    def enqueue(self, event):
        with self.lock:
            if not self.config["enabled"]:
                return
            # Pin the destination at capture time, including across later config changes.
            config = self.config
            date = event["timestamp"][:10]
            object_name = "/".join(part for part in (config["prefix"], date, event["id"] + ".jpg") if part)
            event["cloud"] = {"status": "pending", "bucket": config["bucket"], "object": object_name, "attempts": 0}
            write_json(self.evidence.root / event["id"] / "event.json", event)
        self.wake.set()

    def retry(self):
        with self.lock:
            for path in self.evidence.root.glob("*/event.json"):
                event = read_json(path, {})
                if event.get("cloud", {}).get("status") == "error":
                    event["cloud"]["retry_after"] = 0
                    write_json(path, event)
        self.wake.set()

    @staticmethod
    def _client(config):
        from google.cloud import storage
        if config["credentials_file"]:
            return storage.Client.from_service_account_json(config["credentials_file"], project=config["project"] or None)
        return storage.Client(project=config["project"] or None)

    def _run(self):
        while not self.stop.is_set():
            self.wake.wait(5)
            self.wake.clear()
            with self.lock:
                config = deepcopy(self.config)
            if not config["enabled"]:
                continue
            attempted = 0
            for path in sorted(self.evidence.root.glob("*/event.json")):
                if self.stop.is_set() or attempted >= 10:
                    break
                with self.lock:
                    if not self.config["enabled"]:
                        break
                    event = read_json(path, {})
                cloud = event.get("cloud", {})
                if cloud.get("status") not in ("pending", "error") or cloud.get("retry_after", 0) > time.time():
                    continue
                attempted += 1
                cloud["attempts"] = cloud.get("attempts", 0) + 1
                try:
                    client = self._client(config)
                    blob = client.bucket(cloud["bucket"]).blob(cloud["object"])
                    blob.metadata = {"event_id": event["id"], "timestamp": event["timestamp"], "event_type": event["event_type"]}
                    # Unique name plus precondition makes retries safe after a lost response.
                    try:
                        blob.upload_from_filename(str(path.parent / "crop.jpg"), content_type="image/jpeg", if_generation_match=0, timeout=20, retry=None)
                    except Exception as error:
                        from google.api_core.exceptions import PreconditionFailed
                        if not isinstance(error, PreconditionFailed):
                            raise
                    cloud.update(status="uploaded", uri=f"gs://{cloud['bucket']}/{cloud['object']}", error=None, retry_after=0)
                    message = "Latest crop uploaded successfully."
                except Exception as error:
                    # Keep details in local metadata; the UI gets a concise actionable message.
                    cloud.update(status="error", error=f"{type(error).__name__}: {error}"[:500], retry_after=time.time() + min(300, 15 * 2 ** min(cloud["attempts"], 4)))
                    message = "Upload failed. Check credentials, bucket permissions and connection. Local evidence is safe; retry is automatic."
                with self.lock:
                    event["cloud"] = cloud
                    write_json(path, event)
                    self.status = message

    def close(self):
        self.stop.set()
        self.wake.set()
