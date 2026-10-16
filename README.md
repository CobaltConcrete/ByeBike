# ByeBike

A camera app for marking cycling/pedestrian areas, detecting cyclist encroachment, and saving evidence. Run locally or on Cloud Run, with YOLO inference and private Google Cloud Storage.

**Cloud Run:** see [the hosting guide](docs/GCP_HOSTING.md). The hosted version supports your browser's camera, password sign-in, and durable GCS crops, metadata and path settings. Redeploy with `./deploy-cloud-run.ps1`.

## Local development

The project environment is already set up on this computer:

```powershell
.\dev.ps1
```

The app opens at **http://127.0.0.1:8765**. Development uses `data/dev/` and starts with GCS uploads off. Keep the terminal running; press Ctrl+C to stop. Restart after Python edits; refresh the browser after frontend edits. For a fresh checkout, install Python 3.12, then:

```powershell
.\dev.ps1 -Install
```

Use `./dev.ps1 -Port 8766` for another port, or `-NoBrowser` to start without opening a tab. See [the local development guide](docs/LOCAL_DEVELOPMENT.md) for camera testing, file locations, dependency setup and troubleshooting.

For a normal local run using `data/` and your `.env` cloud configuration, run `./start.ps1` (or `.\.venv\Scripts\python.exe app.py`). Local servers listen only on this computer.

## Path Setup

1. Choose a camera: **Browser camera** accesses this device with browser permission; USB index (`0`, `1`, ...), RTSP/HTTP stream URL, or local video file are opened by the server. USB backend and resolution are editable under **Connection options**. Streams use FFmpeg. Click **Connect camera**. **Try sample image** lets you exercise the whole workflow without hardware.
2. Choose **Pedestrian** or **Cycling**. Draw the visible path surface on the frozen reference frame. **Polygon:** click corners, then click the first point, press Enter, or select Finish shape. **Trace:** drag a closed outline for curves. **Edit:** select an area and drag its points. Add separate areas as needed (up to 16).
3. Use **Undo**, **Delete**, or Escape to correct a draft. **Refresh still** takes a new reference frame while preserving outlines. Save paths before monitoring. Pedestrian and cycling interiors cannot overlap; shared borders are allowed.

At least one pedestrian area is required. A cycling area is optional, but enables the app to distinguish observed cycling-to-pedestrian crossings from cyclists first seen on the walkway. Paths persist separately by camera source. Redraw whenever the camera moves or its view changes. Video-file playback pauses during Path Setup and ends normally; reconnect to replay.

## Live Monitor and the encroachment rule

The detector identifies people and bicycles, pairs spatially overlapping boxes, and tracks the bicycles between processed frames. It samples **21 points across the bicycle box's lower edge**, 3% of the box height above its bottom. The share inside your pedestrian polygons is the **contact overlap**. The colored line under each detected bicycle shows this measured edge.

The default alert requires **at least 25% contact overlap for 0.7 seconds and at least two detections**. Both overlap and confirmation time are editable in the UI. Pause monitoring, adjust them, and select **Apply detection rules**. These are prototype starting values, not a validated enforcement standard:

| Setting | Earlier warning | Fewer boundary-jitter alerts |
| --- | --- | --- |
| Contact overlap | 10–20% | 35–50% |
| Confirmation time | 0.3–0.5 s | 0.8–1.5 s |

Start with 25% / 0.7 s, then replay representative footage and tune against visually reviewed examples. The overlap is an **image-space ground-contact proxy**, not a percentage of segmented bicycle pixels or a physical distance. It avoids treating the rider's upper body as ground intrusion. Perspective, occlusion, and bounding-box errors still affect it. A parked bike alone is excluded, but a person walking a bicycle can be paired and flagged. Simple tracking can swap IDs in crowded scenes or produce a new event after a long occlusion. Review evidence before acting.

Confirmed encroachment turns the overlay red and saves one event per tracked entry. A cyclist must stay outside for 3 seconds before another alert on the same track (editable). Tracks expire after 2 seconds without a match. Capture and inference run independently, using the latest frame rather than accumulating old frames.

## Evidence and Google Cloud Storage

In a normal local run, each event is stored in `data/evidence/EVENT_ID/`; development runs use `data/dev/evidence/EVENT_ID/`:

- `crop.jpg`: original image crop covering the paired cyclist and bicycle, with padding.
- `frame.jpg`: full scene with path outlines and detected encroachment.
- `event.json`: UTC timestamp, camera label, boxes, threshold settings, path coordinates, crossing classification, and upload status.

The dashboard lists the latest 60 events; all completed events remain on disk. `data/settings.json` stores camera/rule settings; `data/paths.json` stores per-source areas. These files, downloaded models, and `.env` are ignored by Git.

**Optional GCS setup:** follow [docs/GCP_SETUP.md](docs/GCP_SETUP.md), then fill in the root `.env`. A safe [.env.example](.env.example) is included for new checkouts. Only new evidence crops upload; local saving always happens first. Failed uploads remain queued on disk for automatic retry. No GCP account is required to run locally.

## Original single-image script

```powershell
.\.venv\Scripts\python.exe detect.py
.\.venv\Scripts\python.exe detect.py example_image.jpg --conf 0.5 --output output/custom.jpg
```

The pretrained YOLO11n weights download on first use; inference runs locally on CPU. The larger YOLO11s option requires its own initial download. The sample image is a statue and validates app plumbing, not real-world cyclist accuracy. No physical speaker/light hardware or automated enforcement is connected.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/app.js
```

Tests cover overlap geometry, rider pairing, sustained entry, duplicate suppression/re-entry, camera validation, path persistence, evidence crops, and cloud failure/retry behavior using a fake cloud client. Live GCS verification requires your bucket and credentials. Physical USB/IP camera accuracy requires testing at the actual site.

API references: [Ultralytics prediction](https://docs.ultralytics.com/modes/predict/), [OpenCV capture](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html), [Flask](https://flask.palletsprojects.com/en/stable/quickstart/).
