# Run ByeBike on localhost

Use the local app while developing and testing. It serves the same dashboard as Cloud Run and runs YOLO on your computer. No GCP login, bucket or app password is required.

## Start on this computer

Open PowerShell and run:

```powershell
cd C:\Users\ganqi\Projects\ByeBike
.\dev.ps1
```

The browser opens **http://127.0.0.1:8765**. Keep the terminal running; press **Ctrl+C** to stop. The server listens only on your computer's loopback interface.

The development launcher uses **`data/dev/`** for paths, settings and evidence, and starts with GCS uploads **disabled**, even if your `.env` or terminal contains Cloud Run/GCS settings. It does not edit `.env`. The temporary environment overrides are restored when the launcher exits. Normal local runs use `data/`; the deployed Cloud Run app uses its GCS bucket.

## First run on a new Windows computer

Install Python 3.12 with the Windows `py` launcher and Git. Open a new PowerShell terminal, then:

```powershell
git clone https://github.com/CobaltConcrete/ByeBike.git
cd ByeBike
.\dev.ps1 -Install
```

`-Install` creates `.venv` if needed, installs CPU PyTorch and the app requirements, then starts the app. Installation needs internet access and may take a few minutes. On later runs use **`./dev.ps1`** without `-Install`; use `-Install` again when dependencies change. You do not need to activate the virtual environment, install Docker, or configure Google Cloud.

If PowerShell blocks script execution, allow locally stored scripts for this terminal session only, then run the command again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\dev.ps1
```

The pretrained YOLO model downloads on first use, so the first prediction needs internet access. Once the selected model is downloaded, local camera detection and local evidence saving can run without GCP.

## Useful startup options

```powershell
# Use another port if 8765 is occupied.
.\dev.ps1 -Port 8766

# Start without opening a browser automatically.
.\dev.ps1 -NoBrowser

# Use a different local data folder for a test session.
.\dev.ps1 -Port 8766 -DataDir .\data\dev-experiment
```

Open the URL matching the chosen port. Run one server per data folder. Do not use the same physical camera from two applications simultaneously.

## Test the workflow

1. Click **Try sample image** to test without camera hardware.
2. In **Path Setup**, draw a pedestrian strip around the bicycle's wheel bottoms (roughly 76–93% down the image). Save paths.
3. Start **Live Monitor**. A confirmed event should appear in the evidence log.
4. Look in `data/dev/evidence/EVENT_ID/` for `crop.jpg`, `frame.jpg` and `event.json`. The event shows **Local** while uploads are off.
5. Switch back to Path Setup to change the contact-overlap threshold or confirmation time.

For your own camera, choose **Browser camera · this device**, allow permission, select a device and connect. Browser camera access works on `localhost`/`127.0.0.1` without HTTPS; keep the tab open while monitoring. You can also use a USB index (`0`, `1`, etc.), a reachable RTSP/HTTP feed, or a local video file path. These sources are opened on your computer. A home-network IP camera can therefore work locally even when it is unreachable from Cloud Run.

## Edit and check changes

- **HTML/CSS/JavaScript:** edit files under `static/` and refresh the browser. Use Ctrl+F5 if needed. Save path edits before refreshing; reconnect a browser camera afterwards.
- **Python:** press Ctrl+C, edit the code, run `./dev.ps1` again and refresh the page. Reconnect the camera and start monitoring again. Saved paths and evidence persist.
- **Requirements:** run `./dev.ps1 -Install` again.

The development server uses an explicit restart workflow. Automatic Python reload and the interactive browser debugger are not enabled, so camera capture and inference are owned by one process.

Run the automated checks from the project folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/app.js
node --check static/login.js
```

Node.js is needed only for the two JavaScript syntax checks, not to run the app. The tests use temporary local folders and mocked GCS storage; they do not write to the deployed bucket.

## Test GCS uploads from your computer

For a normal local run that honors your `.env` GCS configuration, stop the development server and use:

```powershell
.\start.ps1
```

Follow [GCP_SETUP.md](GCP_SETUP.md) for local authentication and `.env` settings. This mode uses `data/` by default and can upload real crops to the configured bucket. Keep `BYEBIKE_HOSTED` unset/false when running locally. To use a separate local folder with the normal configuration:

```powershell
.\start.ps1 -Port 8766 -DataDir .\data\gcs-test
```

The development launcher starts with uploads disabled; an explicit change in the dashboard's GCS settings can still enable uploads for that session. Use a separate test prefix if you choose to exercise cloud uploads.

## Deploy after local testing

```powershell
.\deploy-cloud-run.ps1
```

See [GCP_HOSTING.md](GCP_HOSTING.md) for the live URL, password location, bucket/IAM setup, Console clicks and full deployment instructions. Starting localhost or pushing Git commits does not automatically redeploy Cloud Run.
