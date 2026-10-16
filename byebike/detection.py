"""Geometry, rider association, and per-bicycle encroachment tracking."""

from dataclasses import dataclass
import math

import cv2
import numpy as np


def validate_zones(zones):
    if not isinstance(zones, list) or len(zones) > 16:
        raise ValueError("Use at most 16 path areas.")
    validated = []
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("kind") not in ("cycling", "pedestrian"):
            raise ValueError("Each area must be a cycling or pedestrian path.")
        points = zone.get("points", [])
        if not isinstance(points, list) or not 3 <= len(points) <= 256:
            raise ValueError("Each area needs 3 to 256 points.")
        clean = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Invalid path point.")
            if not all(isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v <= 1 for v in point):
                raise ValueError("Path points must lie within the image.")
            clean.append([float(v) for v in point])
        polygon = np.array(clean, dtype=np.float32)
        if cv2.contourArea(polygon) < 0.0002:
            raise ValueError("A path area is too small. Draw a larger, closed shape.")
        if self_intersects(clean):
            raise ValueError("A path boundary crosses itself. Move or remove the crossing points.")
        validated.append({"id": str(zone.get("id", len(validated)))[:64], "kind": zone["kind"], "points": clean})
    masks = zone_masks(validated, (512, 512))
    # Shared edges are fine; overlapping interiors are ambiguous.
    kernel = np.ones((3, 3), np.uint8)
    if np.any(cv2.erode(masks["cycling"], kernel) & cv2.erode(masks["pedestrian"], kernel)):
        raise ValueError("Cycling and pedestrian areas overlap. Keep their interiors separate.")
    return validated


def self_intersects(points):
    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, p):
        return min(a[0], b[0]) - 1e-10 <= p[0] <= max(a[0], b[0]) + 1e-10 and min(a[1], b[1]) - 1e-10 <= p[1] <= max(a[1], b[1]) + 1e-10

    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        if a == b:
            return True
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue
            c, d = points[j], points[(j + 1) % count]
            o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return True
            if any(abs(o) < 1e-10 and on_segment(s, e, p) for o, s, e, p in ((o1, a, b, c), (o2, a, b, d), (o3, c, d, a), (o4, c, d, b))):
                return True
    return False


def zone_masks(zones, shape):
    height, width = shape[:2]
    masks = {kind: np.zeros((height, width), dtype=np.uint8) for kind in ("cycling", "pedestrian")}
    for zone in zones:
        polygon = np.rint(np.array(zone["points"]) * [width - 1, height - 1]).astype(np.int32)
        cv2.fillPoly(masks[zone["kind"]], [polygon], 1)
    return masks


def contact_fraction(box, mask):
    """Sample the bicycle's lower edge, a ground-contact proxy (not its rider)."""
    height, width = mask.shape
    x1, y1, x2, y2 = box
    xs = np.clip(np.linspace(x1, x2, 21).astype(int), 0, width - 1)
    y = int(np.clip(y2 - (y2 - y1) * 0.03, 0, height - 1))
    return float(mask[y, xs].mean())


def box_iou(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / max(union, 1)


def pair_cyclists(detections):
    """Greedily associate people with overlapping bicycles, one rider per bicycle."""
    bikes = [d for d in detections if d["class_id"] == 1]
    people = [d for d in detections if d["class_id"] == 0]
    candidates = []
    for bi, bike in enumerate(bikes):
        bx1, by1, bx2, by2 = bike["box"]
        for pi, person in enumerate(people):
            px1, py1, px2, py2 = person["box"]
            horizontal = max(0, min(bx2, px2) - max(bx1, px1)) / max(1, min(bx2 - bx1, px2 - px1))
            vertical = max(0, min(by2, py2) - max(by1, py1)) / max(1, min(by2 - by1, py2 - py1))
            person_center = (px1 + px2) / 2
            if horizontal >= 0.45 and vertical >= 0.2 and py1 < by1 + (by2 - by1) * 0.45 and bx1 <= person_center <= bx2:
                candidates.append((horizontal + vertical + person["confidence"] * 0.1, bi, pi))
    used_bikes, used_people, cyclists = set(), set(), []
    for _, bi, pi in sorted(candidates, reverse=True):
        if bi in used_bikes or pi in used_people:
            continue
        used_bikes.add(bi)
        used_people.add(pi)
        bike, person = bikes[bi], people[pi]
        b, p = bike["box"], person["box"]
        cyclists.append({
            "bike_box": b, "person_box": p,
            "box": [min(b[0], p[0]), min(b[1], p[1]), max(b[2], p[2]), max(b[3], p[3])],
            "confidence": min(bike["confidence"], person["confidence"]),
        })
    return cyclists


@dataclass
class Track:
    id: int
    box: list
    last_seen: float
    inside_since: float | None = None
    outside_since: float | None = None
    inside_frames: int = 0
    reported: bool = False
    seen_cycling: bool = False


class EncroachmentTracker:
    def __init__(self):
        self.tracks = {}
        self.next_id = 1

    def update(self, cyclists, masks, now, dwell=0.7, overlap=0.25, rearm=3.0):
        self.tracks = {key: track for key, track in self.tracks.items() if now - track.last_seen <= 2.0}
        matches, available = {}, set(self.tracks)
        candidates = []
        for index, cyclist in enumerate(cyclists):
            box = cyclist["bike_box"]
            for key, track in self.tracks.items():
                iou = box_iou(box, track.box)
                distance = math.hypot((box[0] + box[2] - track.box[0] - track.box[2]) / 2, (box[1] + box[3] - track.box[1] - track.box[3]) / 2)
                scale = max(20, box[2] - box[0], box[3] - box[1])
                if iou >= 0.15 or distance < scale * 0.5:
                    candidates.append((iou - distance / scale * 0.2, index, key))
        for _, index, key in sorted(candidates, reverse=True):
            if index not in matches and key in available:
                matches[index] = key
                available.remove(key)
        for key in available:
            self.tracks[key].inside_since = None
            self.tracks[key].inside_frames = 0
        for index, cyclist in enumerate(cyclists):
            if index not in matches:
                key = self.next_id
                self.next_id += 1
                self.tracks[key] = Track(key, cyclist["bike_box"], now)
                matches[index] = key
            track = self.tracks[matches[index]]
            track.box, track.last_seen = cyclist["bike_box"], now
            fraction = contact_fraction(track.box, masks["pedestrian"])
            inside = fraction >= overlap
            if not inside and contact_fraction(track.box, masks["cycling"]) >= overlap:
                track.seen_cycling = True
            event = False
            if inside:
                track.outside_since = None
                if track.inside_since is None:
                    track.inside_since = now
                track.inside_frames += 1
                confirmed = now - track.inside_since >= dwell and track.inside_frames >= 2
                if confirmed and not track.reported:
                    event = True
                    track.reported = True
            else:
                confirmed = False
                track.inside_since, track.inside_frames = None, 0
                if track.outside_since is None:
                    track.outside_since = now
                if now - track.outside_since >= rearm:
                    track.reported = False
            cyclist.update(track_id=track.id, inside=inside, confirmed=confirmed, event=event, overlap=round(fraction, 3),
                           event_type="crossed_from_cycling" if track.seen_cycling else "first_seen_in_pedestrian_area")
        return cyclists
