"""Small password gate for the single-operator hosted prototype."""

from collections import deque
from datetime import timedelta
import hashlib
import secrets
from threading import Lock
import time

from flask import jsonify, redirect, request, session


def install_auth(app, password, secure):
    if len(password) < 24:
        raise RuntimeError("Hosted ByeBike requires BYEBIKE_APP_PASSWORD with at least 24 characters.")
    app.config.update(SECRET_KEY=hashlib.sha256(("byebike-session:" + password).encode()).hexdigest(),
                      SESSION_COOKIE_NAME="byebike_session", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=secure, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    attempts, lock = deque(), Lock()

    @app.before_request
    def require_login():
        if request.path in ("/login", "/api/login", "/healthz", "/static/style.css", "/static/login.js"):
            return None
        if not session.get("authenticated"):
            if request.path == "/":
                return redirect("/login")
            return jsonify(error="Sign in to continue."), 401

    @app.get("/login")
    def login_page():
        return app.send_static_file("login.html")

    @app.post("/api/login")
    def login():
        now = time.monotonic()
        with lock:
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 20:
                return jsonify(error="Too many sign-in attempts. Try again in one minute."), 429
            attempts.append(now)
        value = request.get_json()
        supplied = value.get("password", "") if isinstance(value, dict) else ""
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied.encode(), password.encode()):
            return jsonify(error="Incorrect password."), 401
        session.clear()
        session["authenticated"] = True
        session.permanent = True
        return jsonify(ok=True)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)
