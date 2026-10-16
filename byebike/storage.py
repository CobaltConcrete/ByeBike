"""Local settings and evidence. No database service is needed."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import cv2


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class EvidenceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, frame, annotated, cyclist, camera, zones, thresholds):
        now = datetime.now(timezone.utc)
        event_id = now.strftime("%Y%m%dT%H%M%S_%fZ_") + uuid4().hex[:8]
        directory = self.root / event_id
        directory.mkdir()
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = cyclist["box"]
        padding = max(12, int(max(x2 - x1, y2 - y1) * 0.08))
        bounds = [max(0, int(x1) - padding), max(0, int(y1) - padding), min(width, int(x2) + padding), min(height, int(y2) + padding)]
        left, top, right, bottom = bounds
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            raise ValueError("Cannot save an empty evidence crop.")
        for name, image in (("crop.jpg", crop), ("frame.jpg", annotated)):
            if not cv2.imwrite(str(directory / name), image, [cv2.IMWRITE_JPEG_QUALITY, 93]):
                raise OSError("Could not write evidence image. Check free disk space and folder permissions.")
        event = {
            "id": event_id, "timestamp": now.isoformat(), "camera": camera["label"],
            "source_kind": camera["kind"], "track_id": cyclist["track_id"],
            "confidence": round(cyclist["confidence"], 4), "event_type": cyclist["event_type"],
            "pedestrian_contact_fraction": cyclist["overlap"], "crop_box": bounds,
            "bicycle_box": cyclist["bike_box"], "person_box": cyclist["person_box"],
            "frame_size": [width, height], "zones": zones,
            "thresholds": thresholds, "overlap_method": "bicycle_lower_edge_21_samples",
            "cloud": {"status": "local"},
            "crop_url": f"/evidence/{event_id}/crop.jpg", "frame_url": f"/evidence/{event_id}/frame.jpg",
            "metadata_url": f"/evidence/{event_id}/event.json",
        }
        # Write metadata last: incomplete saves never appear in the gallery.
        write_json(directory / "event.json", event)
        return event

    def list(self, limit=60):
        files = sorted(self.root.glob("*/event.json"), reverse=True)
        events = [read_json(path, None) for path in files[:limit]]
        return [event for event in events if event], len(files)
