# ByeBike on Cloud Run

The app runs YOLO on Cloud Run and stores cyclist crops, event metadata, camera settings and path outlines in your private GCS bucket. Browser camera frames are sent over HTTPS to the server. No VM or SSH tunnel is needed.

For development on your computer, run `./dev.ps1` and open `http://127.0.0.1:8765`. See [Local development](LOCAL_DEVELOPMENT.md) for installation, camera testing and restart instructions. Local development does not redeploy this service.

**Deployment verified on 16 October 2026:** [Open ByeBike](https://byebike-544226653236.us-central1.run.app). HTTPS sign-in, browser camera uploads, YOLO detection and a real crop upload to GCS were tested. The redeploy script was run successfully, and saved evidence and paths were verified after the new revision started. A sample verification event is in the evidence log. Your current project is already set up; use **Open the app** and **Redeploy** for everyday use. The setup sections below explain how to reproduce it yourself.

| Setting | Value |
| --- | --- |
| Project | `byebike-511808` |
| Region | `us-central1` (Iowa) |
| Cloud Run service | `byebike` |
| Bucket | `byebike-itss-lta` |
| Object prefix | `byebike` |
| Runtime identity | `byebike-run@byebike-511808.iam.gserviceaccount.com` |

## Open the app

Find the HTTPS link in **Google Cloud Console → Cloud Run → byebike**, or run:

```powershell
gcloud run services describe byebike --project=byebike-511808 --region=us-central1 --format="value(status.url)"
```

Sign in with the generated app password in **`credentials/cloud-run-password.txt`**. That directory is ignored by Git and excluded from builds. This is the ByeBike password, not your Google account password. If the local file is unavailable, retrieve the deployed password with your authorized Google account:

```powershell
gcloud secrets versions access latest --secret=byebike-app-password --project=byebike-511808
```

This prints a secret to your terminal; do not paste it into Git or shared logs. Cloud Run accepts public browser connections, but the app requires sign-in for its UI, APIs, live frames and evidence. The bucket is private. This single-operator prototype uses a shared password, not individual user accounts.

## Connect a camera and monitor

1. Choose **Browser camera · this device**. Click **Allow camera & list devices**, allow permission, choose a camera and click **Connect camera**. Cloud Run cannot open your laptop's USB index directly.
2. In **Path Setup**, draw and save pedestrian areas; optionally add cycling areas. Use Polygon, Trace and Edit. Redraw whenever the camera moves.
3. Start **Live Monitor**. The default rule requires at least 25% of the bicycle's lower edge inside a pedestrian area for 0.7 seconds and at least two detections. Change both values in Detection rules while paused.
4. Confirmed events appear with **GCS synced**. Open the crop or metadata from the card.
5. Keep the camera tab open and visible. Closing it, losing permission, or suspending frame uploads pauses monitoring after 10 seconds. Reconnect after a reload or container restart.

**Try sample image** tests detection and uploads without hardware. Draw a pedestrian strip at the wheel bottoms (roughly 76–93% down the image), save and start monitoring. This statue tests the workflow, not real-world accuracy.

**IP camera · RTSP / HTTP** works if the server can reach the feed. A home `192.168.x.x` address needs private network connectivity such as a VPN/VPC arrangement. USB devices and local video paths on your laptop are available only when running locally.

## Stored data and credentials

```text
gs://byebike-itss-lta/byebike/YYYY-MM-DD/EVENT_ID.jpg
gs://byebike-itss-lta/byebike/cloudrun/events/EVENT_ID.json
gs://byebike-itss-lta/byebike/cloudrun/settings.json
gs://byebike-itss-lta/byebike/cloudrun/paths.json
```

Only cyclist crops are uploaded, not full scene snapshots. Metadata includes timestamps, thresholds, camera label and path coordinates. Settings may contain a camera stream URL, so retain private bucket access. The local version still saves full scene snapshots locally.

An event is acknowledged only after its crop and metadata reach GCS. Failed saves pause monitoring and show an error; Cloud Run has no durable local retry queue. Fix access/connectivity and restart monitoring. A failure between the two uploads can leave an unlisted crop in the bucket. Saved events and paths survive redeploys; active camera/tracker state does not. Reconnect and start monitoring again after a restart.

The attached runtime service account supplies credentials. **Do not set `GOOGLE_APPLICATION_CREDENTIALS` on Cloud Run or upload a JSON key.** Your local `.env` is excluded from the image. Hosted storage is configured by deployment variables and read-only in the UI. [Service identity](https://docs.cloud.google.com/run/docs/securing/service-identity), [Secret Manager integration](https://docs.cloud.google.com/run/docs/configuring/services/secrets).

## Redeploy

Pause monitoring and disconnect the camera, then run in PowerShell:

```powershell
cd C:\Users\ganqi\Projects\ByeBike
.\deploy-cloud-run.ps1
```

The script uploads an allowlisted source archive, builds a CPU container in Cloud Build, deploys it, and prints the URL. Docker Desktop need not run. The URL stays the same. Commands explicitly name the project, so your default gcloud project does not affect them.

Both YOLO11 model choices are included in the image. Gunicorn runs **one worker**, 8 request threads, 2 vCPUs and 4 GiB memory. Use one operator and one camera at a time. Keep service and revision maximum instances at one. This app does not support independent concurrent camera sessions or horizontal scaling. Instances may briefly overlap during deployment; pause/disconnect before redeploying.

## Runtime and cost

Minimum instances is **0**, maximum **1**. CPU stays allocated while an instance exists so inference can run between requests. This uses **instance-based billing**. An instance can remain billable during its idle period before scaling down; an open dashboard keeps polling. Disconnect and close the dashboard when finished. Builds, image storage, GCS operations/storage and network traffic can also incur charges. [Billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings).

This deployment supports interactive demonstrations, not guaranteed unattended 24/7 monitoring. Cloud Run may replace an instance, and camera reconnection is currently manual. Cold starts take several seconds. Measure detection speed on your actual footage.

## One-time resources (already provisioned)

Use the complete repository checkout containing `app.py`, `Dockerfile`, `cloudbuild.yaml` and `deploy-cloud-run.ps1`. On another computer, clone `https://github.com/CobaltConcrete/ByeBike.git` and open PowerShell in the `ByeBike` folder. Install the [Google Cloud CLI for Windows](https://docs.cloud.google.com/sdk/docs/install) if `gcloud` is unavailable, then open a new terminal.

Sign in and select the project:

```powershell
gcloud auth login
gcloud config set project byebike-511808
gcloud config get-value project
```

In Cloud Console, use the project selector at the top and select **byebike-511808**, not the old Qwiklabs project. Find a project's ID in that selector or **IAM & Admin → Settings**; the ID differs from the display name and project number. Open **Billing** and ensure a billing account is linked. The setup commands require permission to create resources and grant IAM roles; your current account is a project Owner. `gcloud auth application-default login` is only for local app uploads and is not required to deploy or run this hosted service.

### Create the storage bucket

Your bucket already exists. For an empty setup, open [Cloud Storage → Buckets](https://console.cloud.google.com/storage/browser?project=byebike-511808), then:

1. Click **Create**. Enter `byebike-itss-lta` as the name.
2. Under location, choose **Region**, then **us-central1 (Iowa)**.
3. Choose **Standard** storage. Leave hierarchical namespace and replication off for this prototype.
4. Keep **Prevent public access** enabled and choose **Uniform** access control.
5. Keep Google-managed encryption and the default data-protection options, then **Create**. Soft delete retains deleted objects temporarily and can incur storage charges.

Equivalent command (run only if the bucket does not already exist):

```powershell
gcloud storage buckets create gs://byebike-itss-lta --project=byebike-511808 --location=us-central1 --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention
```

Bucket names are globally unique. In another project you must choose an available name and pass it with `-Bucket` to the deploy script. A random suffix is only needed if your chosen name is taken. `byebike` is just the folder prefix inside the bucket; the app creates objects beneath it automatically. [Bucket creation reference](https://docs.cloud.google.com/storage/docs/creating-buckets).

### Create deployment resources and grant access

These commands document the remaining setup for an empty project. Skip `create` commands for resources that already exist. IAM binding commands can be rerun to restore the listed permissions:

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com --project=byebike-511808
gcloud iam service-accounts create byebike-run --display-name="ByeBike Cloud Run" --project=byebike-511808
gcloud iam service-accounts create byebike-build --display-name="ByeBike container builder" --project=byebike-511808
gcloud artifacts repositories create byebike --repository-format=docker --location=us-central1 --project=byebike-511808
gcloud storage buckets create gs://byebike-511808-cloudbuild-source --project=byebike-511808 --location=us-central1 --uniform-bucket-level-access --public-access-prevention

gcloud storage buckets add-iam-policy-binding gs://byebike-itss-lta --member=serviceAccount:byebike-run@byebike-511808.iam.gserviceaccount.com --role=roles/storage.objectUser
gcloud storage buckets add-iam-policy-binding gs://byebike-511808-cloudbuild-source --member=serviceAccount:byebike-build@byebike-511808.iam.gserviceaccount.com --role=roles/storage.objectViewer
gcloud artifacts repositories add-iam-policy-binding byebike --location=us-central1 --project=byebike-511808 --member=serviceAccount:byebike-build@byebike-511808.iam.gserviceaccount.com --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding byebike-511808 --member=serviceAccount:byebike-build@byebike-511808.iam.gserviceaccount.com --role=roles/logging.logWriter
```

Runtime object access is limited to the evidence bucket. The builder gets source archive, image repository and log access, not evidence or password access. [Build identities](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts).

Where to find these resources in the Console:

| Resource / action | Console location and clicks |
| --- | --- |
| Enable an API | **APIs & Services → Library**; search Cloud Run Admin API, Cloud Build API, Artifact Registry API and Secret Manager API; open each and click **Enable**. |
| Create service accounts | **IAM & Admin → Service Accounts → Create service account**; create `byebike-run` and `byebike-build`. Skip project role grants during creation; use the specific grants below. Do not create JSON keys. |
| Runtime bucket access | **Cloud Storage → Buckets → byebike-itss-lta → Permissions → Grant access**. Principal: `byebike-run@byebike-511808.iam.gserviceaccount.com`. Role: **Storage Object User**. Save. |
| Build source bucket | Create `byebike-511808-cloudbuild-source` in `us-central1` with Uniform access and public access prevention. In its **Permissions**, grant `byebike-build@byebike-511808.iam.gserviceaccount.com` **Storage Object Viewer**. |
| Container repository | **Artifact Registry → Repositories → Create repository**. Name `byebike`, format **Docker**, mode **Standard**, location type **Region**, region `us-central1`. Create. Select the repository and use **Permissions / Grant access** to give the build account **Artifact Registry Writer**. |
| Build logs | **IAM & Admin → IAM → Grant access**. Principal: the build account above. Role: **Logs Writer**. Save. |

Use **Storage Object User** for the Cloud Run runtime, rather than the **Storage Object Creator** role in the local upload-only guide: the hosted app also reads events and updates settings. [Bucket IAM instructions](https://docs.cloud.google.com/storage/docs/access-control/using-iam-permissions).

### Create the app password secret

For a **new** setup, generate and store the password:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; import secrets; p=Path('credentials/cloud-run-password.txt'); p.parent.mkdir(exist_ok=True); p.open('x',encoding='utf-8').write(secrets.token_urlsafe(32))"
gcloud secrets create byebike-app-password --project=byebike-511808 --replication-policy=automatic --data-file=credentials/cloud-run-password.txt
gcloud secrets add-iam-policy-binding byebike-app-password --project=byebike-511808 --member=serviceAccount:byebike-run@byebike-511808.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
```

The generator refuses to overwrite an existing file. To rotate intentionally, replace that file with a new random password (at least 24 characters), add a secret version using `gcloud secrets versions add byebike-app-password --project=byebike-511808 --data-file=credentials/cloud-run-password.txt`, and redeploy. The new revision invalidates previous login sessions.

Console equivalent: **Security → Secret Manager → Create secret**. Name it `byebike-app-password`, enter a strong random app password of at least 24 characters, leave automatic replication selected and create it. On the secret's **Permissions** panel, grant `byebike-run@byebike-511808.iam.gserviceaccount.com` **Secret Manager Secret Accessor**. To view it later, open **Versions**, select a version and use **View secret value**. This role should be on this secret, not project-wide. [Create a secret](https://docs.cloud.google.com/secret-manager/docs/creating-and-accessing-secrets).

### Build and deploy the app yourself

After creating the resources, run `./deploy-cloud-run.ps1`. It performs both steps below automatically. If you want to run them separately, use these PowerShell commands:

```powershell
cd C:\Users\ganqi\Projects\ByeBike
$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "us-central1-docker.pkg.dev/byebike-511808/byebike/app:$tag"
gcloud builds submit . --project=byebike-511808 --region=us-central1 --config=cloudbuild.yaml --service-account=projects/byebike-511808/serviceAccounts/byebike-build@byebike-511808.iam.gserviceaccount.com --gcs-source-staging-dir=gs://byebike-511808-cloudbuild-source/source --substitutions="_IMAGE=$image"
```

Wait for **SUCCESS**. The initial build takes several minutes. Then:

```powershell
gcloud run deploy byebike --image=$image --project=byebike-511808 --region=us-central1 `
  --service-account=byebike-run@byebike-511808.iam.gserviceaccount.com `
  --cpu=2 --memory=4Gi --concurrency=8 --min=0 --max=1 --max-instances=1 `
  --no-cpu-throttling --session-affinity --timeout=120 --port=8080 `
  --set-env-vars="BYEBIKE_HOSTED=true,BYEBIKE_GCS_PROJECT=byebike-511808,BYEBIKE_GCS_BUCKET=byebike-itss-lta,BYEBIKE_GCS_PREFIX=byebike,XDG_RUNTIME_DIR=/tmp" `
  --set-secrets="BYEBIKE_APP_PASSWORD=byebike-app-password:latest" `
  --startup-probe="httpGet.path=/healthz,httpGet.port=8080,initialDelaySeconds=0,periodSeconds=5,timeoutSeconds=3,failureThreshold=30" `
  --allow-unauthenticated
```

**Keep the quotes around comma-separated flag values in PowerShell.** Without them, PowerShell can turn commas into spaces and Cloud Run receives broken environment settings. A backtick must be the last character on its line, with no trailing spaces. Using the script avoids copy/paste mistakes. `--allow-unauthenticated` allows the browser to reach ByeBike's password screen; it does not bypass the app's sign-in.

### Deploy or inspect the container in the Console

Use the CLI to build the image from your local source. After that build succeeds, you can deploy the image using Console instead of the deploy command:

1. Open [Cloud Run](https://console.cloud.google.com/run?project=byebike-511808). For a new service choose **Deploy container**; for this existing app, click **byebike → Edit & deploy new revision**.
2. Select the latest image under **Artifact Registry → us-central1 → byebike → app**. Set service name `byebike` and region `us-central1` when creating it. Allow public access so the app's own sign-in screen can load.
3. Open **Containers, Networking, Security** / the container settings. Match the settings table below. Add the environment variables and secret reference.
4. Click **Create** or **Deploy**. Wait for the revision to become ready. The service details page shows the HTTPS **URL** at the top. [Deploying container images](https://docs.cloud.google.com/run/docs/deploying).

The script sets these exact values; use it if a Console setting is difficult to locate:

| Setting | Value |
| --- | --- |
| Container port | `8080` |
| CPU / memory | `2` vCPU / `4 GiB` |
| Billing / CPU allocation | Instance-based billing / CPU always allocated |
| Service min / max instances | `0` / `1` |
| Revision max instances | `1` |
| Concurrent requests per instance | `8` |
| Request timeout | `120` seconds |
| Session affinity | Enabled |
| Runtime service account (Security) | `byebike-run@byebike-511808.iam.gserviceaccount.com` |
| Startup health check | HTTP GET `/healthz`, port `8080`, period `5s`, timeout `3s`, failure threshold `30` |
| Environment variable | `BYEBIKE_HOSTED` = `true` |
| Environment variable | `BYEBIKE_GCS_PROJECT` = `byebike-511808` |
| Environment variable | `BYEBIKE_GCS_BUCKET` = `byebike-itss-lta` |
| Environment variable | `BYEBIKE_GCS_PREFIX` = `byebike` |
| Environment variable | `XDG_RUNTIME_DIR` = `/tmp` |
| Secret exposed as environment variable | `BYEBIKE_APP_PASSWORD` → secret `byebike-app-password`, version `latest` |

In the container's **Variables & Secrets** tab, choose **Reference a secret** for the password. Do not type the password as an ordinary environment variable, or set a JSON credentials path. Settings changes create a new revision. Stop monitoring before editing/deploying and reconnect afterwards.

### Find images, crops, status and costs

| What you want | Where to click |
| --- | --- |
| Open the app | **Cloud Run → byebike → URL**; sign in with the app password. |
| Build status | **Cloud Build → History**, select region `us-central1`, open the latest build and its logs. |
| Deployed version | **Cloud Run → byebike → Revisions**; the ready revision receiving 100% traffic is the live one. |
| Saved crops | **Cloud Storage → Buckets → byebike-itss-lta → Objects → byebike → date folder → .jpg**. Open the object to view/download it. |
| Paths and event metadata | In the same bucket, open **byebike → cloudrun**; `paths.json`, `settings.json`, and **events** are here. |
| Stored container images | **Artifact Registry → Repositories → byebike → app**. Old images consume storage even after redeploying. |
| Runtime errors | **Cloud Run → byebike → Logs**. |
| Spending / alerts | **Billing → Reports** and **Billing → Budgets & alerts**. A budget alert is a notification, not a spending cap. |

You do not need to make image objects public or configure bucket CORS: the signed-in app reads them through its server.

## Logs

```powershell
gcloud run services logs read byebike --project=byebike-511808 --region=us-central1 --limit=50
```

Use **Cloud Build → History** for build failures and **Cloud Run → byebike → Logs** for runtime failures. Storage errors need the runtime identity's bucket access, not a local ADC login. `/healthz` is a public health check; the other app APIs and evidence require sign-in.

| Symptom | Check / fix |
| --- | --- |
| Wrong project or Qwiklabs resources | `gcloud config set project byebike-511808`; use the project selector in Console too. |
| API disabled | Run the `gcloud services enable ...` command above. |
| Storage 403 / monitor pauses while saving | Check **Storage Object User** for the runtime account on `byebike-itss-lta`. Fix access, then restart monitoring. |
| Secret access denied during startup | Check **Secret Manager Secret Accessor** on `byebike-app-password` for the runtime account. |
| Build cannot upload image / read source | Check the build account's Artifact Registry Writer and source-bucket Object Viewer bindings. |
| App returns 503 | Check Cloud Run logs and revision readiness. Verify each environment variable is separate; quote the comma-separated PowerShell flag. |
| Browser camera unavailable | Open the HTTPS app URL, allow camera permissions, close other camera apps, and select Browser camera. |
| IP camera does not connect | The feed must be reachable from GCP; a private home/LAN address is not directly reachable. |
| App returns to Path Setup after restart | Expected: saved paths/evidence persist; reconnect the camera and start monitoring manually. |
