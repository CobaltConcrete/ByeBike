# ByeBike

Run YOLO object detection on the supplied `example_image.jpg`. The script prints
detected classes, confidence scores, and bounding boxes, and saves an annotated
image to `output/detections.jpg`.

## Setup and run (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe detect.py
```

The script runs on CPU and downloads the small pretrained YOLO11n weights on its
first run, which requires internet access.

To use a different image, confidence threshold, or output path:

```powershell
.\.venv\Scripts\python.exe detect.py example_image.jpg --conf 0.15 --output output/custom.jpg
```

Use `--model yolo11s.pt` to try a larger detection model. See the
[Ultralytics prediction documentation](https://docs.ultralytics.com/modes/predict/)
for the underlying API.
