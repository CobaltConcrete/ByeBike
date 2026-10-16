"""Camera capture and inference run independently so live frames do not queue up."""

from copy import deepcopy
import hashlib
import math
import secrets
from pathlib import Path
from threading import Event, RLock, Thread
import time

import cv2
import numpy as np

from .detection import EncroachmentTracker, pair_cyclists, validate_zones, zone_masks
from .storage import read_json, write_json


DEFAULT_CAMERA = {"kind": "usb", "source": "0", "backend": "auto", "label": "Shared pathway", "width": 1280, "height": 720}
DEFAULT_RULES = {"confidence": 0.4, "overlap": 0.25, "dwell": 0.7, "rearm": 3.0, "model": "yolo11n.pt"}
COLORS = {"cycling": (126, 185, 87), "pedestrian": (225, 176, 66)}


def encode(frame):
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buffer.tobytes() if ok else None


def validate_camera(config):
    result = {**DEFAULT_CAMERA, **{key: config[key] for key in DEFAULT_CAMERA if key in config}}
    if result["kind"] not in ("usb", "stream", "video", "demo", "browser") or result["backend"] not in ("auto", "dshow", "msmf", "ffmpeg"):
        raise ValueError("Choose a valid camera type and backend.")
    result["source"] = str(result["source"]).strip()
    if len(result["source"]) > 2000:
        raise ValueError("Camera source is too long.")
    if result["kind"] == "usb" and (not result["source"].isdigit() or int(result["source"]) > 99):
        raise ValueError("USB camera index must be a number from 0 to 99.")
    if result["kind"] in ("stream", "video") and not result["source"]:
        raise ValueError("Enter a stream URL or local video path.")
    if result["kind"] == "stream" and not result["source"].lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        raise ValueError("Use an RTSP or HTTP(S) camera stream URL.")
    if result["kind"] == "video" and not Path(result["source"]).is_file():
        raise ValueError("Video file not found on this computer.")
    if not isinstance(result["label"], str) or not 1 <= len(result["label"].strip()) <= 80:
        raise ValueError("Give the camera a name of 1 to 80 characters.")
    for key in ("width", "height"):
        if not isinstance(result[key], int) or not 240 <= result[key] <= 3840:
            raise ValueError("Requested camera dimensions must be between 240 and 3840 pixels.")
    return result


def validate_rules(config):
    result = {**DEFAULT_RULES, **{key: config[key] for key in DEFAULT_RULES if key in config}}
    for key, low, high in (("confidence", 0.1, 0.95), ("overlap", 0.05, 1), ("dwell", 0.1, 5), ("rearm", 1, 30)):
        value = result[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
    if result["model"] not in ("yolo11n.pt", "yolo11s.pt"):
        raise ValueError("Select YOLO11 nano or small.")
    return result


def annotate(frame, zones, cyclists):
    output = frame.copy()
    tint = frame.copy()
    height, width = frame.shape[:2]
    for zone in zones:
        points = np.rint(np.array(zone["points"]) * [width - 1, height - 1]).astype(np.int32)
        cv2.fillPoly(tint, [points], COLORS[zone["kind"]])
    cv2.addWeighted(tint, 0.18, output, 0.82, 0, output)
    for zone in zones:
        points = np.rint(np.array(zone["points"]) * [width - 1, height - 1]).astype(np.int32)
        cv2.polylines(output, [points], True, COLORS[zone["kind"]], 2)
    for cyclist in cyclists:
        color = (80, 80, 245) if cyclist["confirmed"] else (70, 191, 255) if cyclist["inside"] else (126, 210, 112)
        x1, y1, x2, y2 = map(int, cyclist["box"])
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        status = "ENCROACHMENT" if cyclist["confirmed"] else "Confirming" if cyclist["inside"] else "Cyclist"
        label = f"#{cyclist['track_id']} {status} | {cyclist['overlap']:.0%}"
        text_y = max(20, y1 - 9)
        (label_width, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        text_x = max(0, min(x1, width - label_width - 8))
        cv2.rectangle(output, (text_x, text_y - 18), (min(width, text_x + label_width + 8), text_y + 5), color, -1)
        cv2.putText(output, label, (text_x + 4, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (25, 30, 35), 1, cv2.LINE_AA)
        bx1, by1, bx2, by2 = cyclist["bike_box"]
        contact_y = int(by2 - (by2 - by1) * 0.03)
        cv2.line(output, (int(bx1), contact_y), (int(bx2), contact_y), color, 4)
    return output


class CameraService:
    def __init__(self, root, data_dir, evidence, cloud, persistence=None, hosted=False):
        self.root, self.data_dir = Path(root), Path(data_dir)
        self.evidence, self.cloud = evidence, cloud
        self.persistence, self.hosted = persistence, hosted
        self.lock = RLock()
        settings = self._read("settings.json", {})
        self.camera = {**DEFAULT_CAMERA, **({"kind": "browser", "source": "default"} if hosted else {}), **settings.get("camera", {})}
        self.rules = {**DEFAULT_RULES, **settings.get("rules", {})}
        self.profiles = self._read("paths.json", {})
        self.source_key = self._source_key(self.camera)
        self.zones = self.profiles.get(self.source_key, {}).get("zones", [])
        self.mode, self.status, self.message = "setup", "disconnected", "Connect a camera to begin."
        self.raw, self.preview, self.rendered = None, None, None
        self.sequence, self.revision = 0, 0
        self.frame_size = None
        self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}
        self.capture_stop = Event()
        self.shutdown = Event()
        self.capture_thread = None
        self.browser_token, self.last_browser_frame = None, 0
        self.inference_thread = Thread(target=self._inference, daemon=True, name="yolo-monitor")
        self.inference_thread.start()

    @staticmethod
    def _source_key(config):
        source = "sample-image" if config["kind"] == "demo" else config["source"]
        return hashlib.sha256(f"{config['kind']}:{source}".encode()).hexdigest()[:20]

    def _save_settings(self):
        self._write("settings.json", {"camera": self.camera, "rules": self.rules})

    def _read(self, filename, default):
        return self.persistence.read(filename, default) if self.persistence else read_json(self.data_dir / filename, default)

    def _write(self, filename, value):
        if self.persistence:
            self.persistence.write(filename, value)
        else:
            write_json(self.data_dir / filename, value)

    def state(self):
        with self.lock:
            return deepcopy({"camera": self.camera, "rules": self.rules, "zones": self.zones, "mode": self.mode,
                             "status": self.status, "message": self.message, "frame_size": self.frame_size,
                             "source_key": self.source_key, "metrics": self.metrics, "has_frame": self.raw is not None})

    def connect(self, config):
        config = validate_camera(config)
        if self.hosted and config["kind"] in ("usb", "video"):
            raise ValueError("Use Browser camera to access your laptop camera on Cloud Run.")
        self.disconnect()
        if self.capture_thread and self.capture_thread.is_alive():
            raise ValueError("The previous camera is still closing. Wait a few seconds and reconnect.")
        with self.lock:
            self.camera = config
            self.source_key = self._source_key(config)
            self.zones = self.profiles.get(self.source_key, {}).get("zones", [])
            self.raw = self.preview = self.rendered = None
            self.frame_size = None
            self.status, self.message = "connecting", "Opening camera..."
            self._save_settings()
            self.capture_stop = Event()
            if config["kind"] == "browser":
                self.browser_token = secrets.token_urlsafe(32)
                self.last_browser_frame = time.monotonic()
                self.message = "Waiting for frames from your browser..."
                return {**self.state(), "capture_token": self.browser_token}
            self.capture_thread = Thread(target=self._capture, args=(deepcopy(config), self.capture_stop), daemon=True, name="camera-capture")
            self.capture_thread.start()
        return self.state()

    def disconnect(self):
        self.capture_stop.set()
        with self.lock:
            self.mode, self.status, self.message = "setup", "disconnected", "Camera disconnected."
            self.revision += 1
            self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}
            self.browser_token = None
        if self.capture_thread:
            self.capture_thread.join(timeout=0.5)

    def save_zones(self, zones, source_key):
        zones = validate_zones(zones)
        with self.lock:
            if self.mode != "setup":
                raise ValueError("Return to Path Setup before changing areas.")
            if source_key != self.source_key or self.status != "connected" or not self.frame_size:
                raise ValueError("Reconnect this camera and refresh the reference image before saving paths.")
            profiles = {**self.profiles, self.source_key: {"zones": zones, "frame_size": self.frame_size}}
            self._write("paths.json", profiles)
            self.zones, self.profiles = zones, profiles
            self.revision += 1
        return self.state()

    def set_rules(self, rules):
        rules = validate_rules(rules)
        with self.lock:
            if self.mode == "monitor":
                raise ValueError("Pause monitoring before changing detection settings.")
            self.rules = rules
            self._save_settings()
            self.revision += 1
        return self.state()

    def set_mode(self, mode):
        if mode not in ("setup", "monitor"):
            raise ValueError("Unknown mode.")
        with self.lock:
            if self.mode == mode:
                return self.state()
            if mode == "monitor":
                if self.status != "connected" or self.raw is None:
                    raise ValueError("Connect a camera before starting Live Monitor.")
                if not any(zone["kind"] == "pedestrian" for zone in self.zones):
                    raise ValueError("Draw and save at least one pedestrian area first.")
            self.mode = mode
            self.revision += 1
            self.rendered = None
            self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}
            self.message = "Loading detector..." if mode == "monitor" else "Camera ready. Mark the paths on a frozen frame."
        return self.state()

    def frame(self, raw=False):
        with self.lock:
            return self.preview if raw or self.mode == "setup" else self.rendered or self.preview

    def browser_frame(self, image, token):
        with self.lock:
            if not self.browser_token or not secrets.compare_digest(token, self.browser_token):
                raise ValueError("Camera session ended. Reconnect Browser camera.")
            if self.camera["kind"] != "browser":
                raise ValueError("Browser camera is not connected.")
            height, width = image.shape[:2]
            if max(width, height) > 1280:
                scale = 1280 / max(width, height)
                image = cv2.resize(image, (round(width * scale), round(height * scale)))
                height, width = image.shape[:2]
            size = [width, height]
            previous = self.frame_size or self.profiles.get(self.source_key, {}).get("frame_size")
            if previous and abs(width / height - previous[0] / previous[1]) > .02:
                self.zones, self.mode = [], "setup"
                self.revision += 1
                self.message = "Camera aspect ratio changed. Redraw and save the paths."
            elif self.status != "connected":
                self.message = "Browser camera ready. Keep this tab open while monitoring."
            self.frame_size = size
            self.raw, self.preview, self.status = image, encode(image), "connected"
            self.sequence += 1
            self.last_browser_frame = time.monotonic()

    def _capture(self, config, stop):
        capture = None
        try:
            demo = config["kind"] == "demo"
            still = cv2.imread(str(self.root / "example_image.jpg")) if demo else None
            fps = 10.0
            if not demo:
                source = int(config["source"]) if config["kind"] == "usb" else config["source"]
                backend = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "ffmpeg": cv2.CAP_FFMPEG}[config["backend"]]
                if config["kind"] == "stream":
                    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
                else:
                    capture = cv2.VideoCapture(source, backend)
                if not capture.isOpened():
                    raise RuntimeError("Cannot open camera. Check its index/URL, backend and whether another app is using it.")
                if config["kind"] == "usb":
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config["width"])
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config["height"])
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                reported_fps = capture.get(cv2.CAP_PROP_FPS)
                fps = reported_fps if math.isfinite(reported_fps) and 1 <= reported_fps <= 120 else 25.0
            while not stop.is_set():
                with self.lock:
                    freeze_video = config["kind"] == "video" and self.mode == "setup" and self.raw is not None
                if freeze_video:
                    stop.wait(0.1)
                    continue
                started = time.monotonic()
                ok, frame = (still is not None, still) if demo else capture.read()
                if not ok:
                    message = "Video ended. Reconnect to replay it." if config["kind"] == "video" else "Camera stopped delivering frames. Check the connection and reconnect."
                    raise RuntimeError(message)
                if max(frame.shape[:2]) > 1280:
                    scale = 1280 / max(frame.shape[:2])
                    frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)))
                preview = encode(frame)
                with self.lock:
                    if stop.is_set():
                        break
                    if self.frame_size is None:
                        height, width = frame.shape[:2]
                        previous_size = self.profiles.get(self.source_key, {}).get("frame_size")
                        self.frame_size = [width, height]
                        self.message = "Sample image connected. This is a workflow demo, not live footage." if demo else "Camera ready."
                        if previous_size and abs(width / height - previous_size[0] / previous_size[1]) > 0.02:
                            self.zones = []
                            self.message = "Camera aspect ratio changed. Please redraw and save the paths."
                    self.raw, self.preview, self.status = frame, preview, "connected"
                    self.sequence += 1
                # Local video playback follows source FPS; live devices already block on capture.
                delay = (1 / fps if demo or config["kind"] == "video" else 0.01) - (time.monotonic() - started)
                stop.wait(max(0.001, delay))
        except Exception as error:
            with self.lock:
                if not stop.is_set():
                    self.status, self.mode = "error", "setup"
                    self.message = str(error) if isinstance(error, RuntimeError) else "Camera error. Check the source and selected backend."
                    self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}
                    self.revision += 1
        finally:
            if capture is not None:
                capture.release()

    def _inference(self):
        model, model_name, last_sequence, revision = None, None, -1, -1
        tracker, masks = EncroachmentTracker(), None
        while not self.shutdown.wait(0.02):
            with self.lock:
                if self.browser_token and time.monotonic() - self.last_browser_frame > 10:
                    self.mode, self.status = "setup", "error"
                    self.message = "Browser camera stopped sending frames. Reconnect the camera."
                    self.browser_token = None
                    self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}
                    self.revision += 1
                if self.mode != "monitor" or self.status != "connected" or self.sequence == last_sequence:
                    continue
                frame = self.raw.copy()
                sequence, current_revision = self.sequence, self.revision
                zones, rules, camera = deepcopy(self.zones), deepcopy(self.rules), deepcopy(self.camera)
            started = time.monotonic()
            try:
                if revision != current_revision:
                    tracker, masks = EncroachmentTracker(), zone_masks(zones, frame.shape)
                    revision = current_revision
                if model_name != rules["model"]:
                    from ultralytics import YOLO
                    model = YOLO(str(self.root / rules["model"]))
                    model_name = rules["model"]
                result = model.predict(frame, classes=[0, 1], conf=rules["confidence"], device="cpu", imgsz=640, verbose=False)[0]
                detections = [{"box": box.xyxy[0].tolist(), "class_id": int(box.cls.item()), "confidence": float(box.conf.item())} for box in result.boxes]
                cyclists = tracker.update(pair_cyclists(detections), masks, time.monotonic(), dwell=rules["dwell"], overlap=rules["overlap"], rearm=rules["rearm"])
                output = annotate(frame, zones, cyclists)
                jpeg = encode(output)
                with self.lock:
                    if self.revision != current_revision or self.mode != "monitor" or self.status != "connected":
                        continue
                    for cyclist in cyclists:
                        if cyclist["event"]:
                            event = self.evidence.save(frame, output, cyclist, camera, zones, rules)
                            self.cloud.enqueue(event)
                    self.rendered = jpeg
                    self.metrics = {"cyclists": len(cyclists), "encroaching": sum(c["confirmed"] for c in cyclists), "fps": round(1 / max(0.001, time.monotonic() - started), 1)}
                    self.message = "Encroachment detected. Evidence saved." if self.metrics["encroaching"] else "Monitoring pedestrian areas."
                last_sequence = sequence
            except Exception as error:
                with self.lock:
                    if self.revision == current_revision:
                        self.mode = "setup"
                        self.message = f"Monitor paused: {type(error).__name__}: {error}"
                        self.metrics = {"cyclists": 0, "encroaching": 0, "fps": 0}

    def close(self):
        self.shutdown.set()
        self.disconnect()
