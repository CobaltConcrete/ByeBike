"""Launch ByeBike's local camera dashboard: python app.py"""

import argparse
import atexit
from io import BytesIO
import os
from pathlib import Path
from threading import Timer
import webbrowser

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import dotenv_values
from PIL import Image, UnidentifiedImageError
import cv2
import numpy as np

from byebike.camera import CameraService
from byebike.cloud import CloudUploader
from byebike.storage import EvidenceStore

ROOT = Path(__file__).resolve().parent


def create_app(data_dir=None):
    # Ignore blank placeholders, especially GOOGLE_APPLICATION_CREDENTIALS.
    for key, value in dotenv_values(ROOT / ".env").items():
        if value:
            os.environ.setdefault(key, value)
    app = Flask(__name__, static_folder="static")
    hosted = os.environ.get("BYEBIKE_HOSTED") == "true"
    if os.environ.get("K_SERVICE") and not hosted:
        raise RuntimeError("Cloud Run must use BYEBIKE_HOSTED=true.")
    trusted = ["127.0.0.1", "localhost", "[::1]"]
    if hosted:
        trusted.extend(os.environ.get("BYEBIKE_TRUSTED_HOSTS", ".run.app").split(","))
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024, TRUSTED_HOSTS=trusted)
    data_dir = Path(data_dir or os.environ.get("BYEBIKE_DATA_DIR", ROOT / "data"))
    persistence = None
    if hosted:
        from byebike.hosted import GcsStore, GcsEvidenceStore, HostedCloud
        from byebike.auth import install_auth
        # Install this after the origin/JSON guard below, before registering routes.
        password = os.environ.get("BYEBIKE_APP_PASSWORD", "")
        if len(password) < 24:
            raise RuntimeError("Set BYEBIKE_APP_PASSWORD through Secret Manager before deploying.")
        persistence = GcsStore(os.environ["BYEBIKE_GCS_BUCKET"], os.environ.get("BYEBIKE_GCS_PREFIX") or "byebike",
                               os.environ["BYEBIKE_GCS_PROJECT"])
        evidence = GcsEvidenceStore(data_dir / "evidence", persistence)
        cloud = HostedCloud(persistence)
    else:
        evidence = EvidenceStore(data_dir / "evidence")
        cloud = CloudUploader(evidence, data_dir / "cloud.json")
    camera = CameraService(ROOT, data_dir, evidence, cloud, persistence=persistence, hosted=hosted)
    app.extensions.update(camera=camera, cloud=cloud, evidence=evidence)
    atexit.register(camera.close)
    atexit.register(cloud.close)

    @app.before_request
    def same_origin_requests():
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Use this app's own browser tab."), 403
            if request.path == "/api/camera/frame":
                if request.mimetype != "image/jpeg":
                    return jsonify(error="Expected a JPEG frame."), 415
            elif not request.is_json:
                return jsonify(error="Expected JSON."), 415

    if hosted:
        install_auth(app, password, secure=bool(os.environ.get("K_SERVICE")))

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        return response

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    @app.errorhandler(ValueError)
    def validation_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/healthz")
    def health():
        return jsonify(ok=True)

    @app.get("/api/state")
    def state():
        events, count = evidence.list()
        return jsonify(**camera.state(), hosted=hosted, cloud=cloud.state(), events=events, event_count=count)

    @app.post("/api/camera")
    def connect():
        return jsonify(camera.connect(body()))

    @app.post("/api/camera/frame")
    def browser_frame():
        try:
            with Image.open(BytesIO(request.get_data())) as uploaded:
                if uploaded.format != "JPEG" or max(uploaded.size) > 3840 or min(uploaded.size) < 16:
                    raise ValueError("Send a JPEG frame no larger than 3840 pixels per side.")
                image = cv2.cvtColor(np.asarray(uploaded.convert("RGB")), cv2.COLOR_RGB2BGR)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError("Invalid camera JPEG.") from error
        camera.browser_frame(image, request.headers.get("X-Camera-Token", ""))
        return jsonify(ok=True)

    @app.post("/api/disconnect")
    def disconnect():
        camera.disconnect()
        return jsonify(camera.state())

    @app.get("/api/frame.jpg")
    def frame():
        image = camera.frame(raw=request.args.get("raw") == "1")
        if image is None:
            return jsonify(error="No camera frame yet."), 503
        return Response(image, mimetype="image/jpeg")

    @app.post("/api/zones")
    def zones():
        value = body()
        return jsonify(camera.save_zones(value.get("zones"), value.get("source_key")))

    @app.post("/api/mode")
    def mode():
        return jsonify(camera.set_mode(body().get("mode")))

    @app.post("/api/rules")
    def rules():
        return jsonify(camera.set_rules(body()))

    @app.post("/api/cloud")
    def cloud_settings():
        return jsonify(cloud.configure(body()))

    @app.post("/api/cloud/retry")
    def retry_uploads():
        cloud.retry()
        return jsonify(ok=True)

    @app.get("/evidence/<event_id>/<filename>")
    def evidence_file(event_id, filename):
        if filename not in ("crop.jpg", "frame.jpg", "event.json"):
            return jsonify(error="Unknown evidence file."), 404
        if hosted:
            result = evidence.read_file(event_id, filename)
            return Response(result[0], mimetype=result[1]) if result else (jsonify(error="Evidence not found."), 404)
        return send_from_directory(evidence.root, f"{event_id}/{filename}")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="Local settings and evidence folder")
    args = parser.parse_args()
    application = create_app(args.data_dir)
    if not args.no_browser:
        timer = Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        timer.daemon = True
        timer.start()
    application.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
