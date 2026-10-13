"""Run YOLO object detection on a single image and save annotated results."""

import argparse
from pathlib import Path

from ultralytics import YOLO


def main():
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "image", nargs="?", type=Path, default=project_dir / "example_image.jpg",
        help="Input image (default: example_image.jpg beside this script)",
    )
    parser.add_argument(
        "--output", type=Path, default=project_dir / "output" / "detections.jpg",
        help="Where to save the annotated image",
    )
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO model weights")
    parser.add_argument("--conf", type=float, default=0.25, help="Minimum confidence (0-1)")
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"Image not found: {args.image}")
    if not 0 <= args.conf <= 1:
        parser.error("--conf must be between 0 and 1")
    if args.image.resolve() == args.output.resolve():
        parser.error("--output must differ from the input image")

    # Pretrained weights download automatically on the first run.
    model = YOLO(args.model)
    result = model.predict(source=str(args.image), conf=args.conf, device="cpu", verbose=False)[0]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.save(filename=str(args.output))

    if len(result.boxes) == 0:
        print("No objects detected. Try lowering --conf.")
    for box in result.boxes:
        label = result.names[int(box.cls.item())]
        confidence = box.conf.item()
        coordinates = [round(value, 1) for value in box.xyxy[0].tolist()]
        print(f"{label}: {confidence:.1%}, box [x1, y1, x2, y2] = {coordinates}")
    print(f"Saved annotated image to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
