FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    YOLO_CONFIG_DIR=/tmp/ultralytics BYEBIKE_DATA_DIR=/tmp/byebike \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-cloud.txt ./
# Install the CPU wheels first so pip does not pull CUDA libraries into this CPU service.
RUN pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-cloud.txt
COPY app.py example_image.jpg ./
COPY byebike/ byebike/
COPY static/ static/
# Bake both UI choices into the image; no first-request model download.
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n.pt').predict('example_image.jpg',device='cpu',verbose=False); YOLO('yolo11s.pt')"
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120 'app:create_app()'"]
