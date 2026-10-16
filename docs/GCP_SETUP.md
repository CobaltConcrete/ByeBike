# Google Cloud Storage setup for ByeBike

This guide connects the **local app** to a private Google Cloud Storage bucket. For the hosted app and redeploy commands, use [Cloud Run hosting](GCP_HOSTING.md). Cloud Run uses its service account automatically; the local ADC steps below are unnecessary for the deployed service.

To run the UI and YOLO **on GCP as well**, follow [GCP_HOSTING.md](GCP_HOSTING.md). This guide covers storage for the local app.

## 1. Create a project and bucket

In [Google Cloud Console](https://console.cloud.google.com/), create or select a project with billing enabled. Copy its **project ID**, not the display name.

Go to **Cloud Storage > Buckets > Create**. Use the bucket name `byebike-itss-lta`. Choose **Region**, `us-central1` (Iowa), and **Standard** storage. Keep **public access prevention** enabled and use **Uniform** access control. Leave advanced replication and retention-lock features off for this prototype. Create the bucket. Storage and requests are billed to the project. [Google&#39;s bucket creation guide](https://cloud.google.com/storage/docs/creating-buckets)

## 2. Authenticate this computer

For a quick local demo, use your Google account with Application Default Credentials (ADC). The Google Cloud CLI is already installed on this computer. Run in PowerShell, replacing the project ID:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Complete the browser sign-in. ADC is what the Python app uses; `gcloud auth login` alone does not configure it. If setting the quota project fails, your account may need **Service Usage Consumer** on that project. [Google&#39;s local ADC guide](https://cloud.google.com/docs/authentication/set-up-adc-local-dev-environment)

The uploading identity needs **Storage Object Creator** (`roles/storage.objectCreator`) on this bucket. In the bucket's **Permissions > Grant access**, add your account with that role if it does not already have permission to create objects. The app writes unique filenames and does not need to list, delete, or overwrite cloud objects. [Cloud Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles)

## 3. Fill in `.env`

A commented `.env` already exists in the project root and is ignored by Git. Fill it in:

```dotenv
BYEBIKE_GCS_ENABLED=true
BYEBIKE_GCS_PROJECT=your-project-id
BYEBIKE_GCS_BUCKET=byebike-itss-lta
BYEBIKE_GCS_PREFIX=byebike
GOOGLE_APPLICATION_CREDENTIALS=
```

For the ADC login above, leave `GOOGLE_APPLICATION_CREDENTIALS` blank. **No API key is needed.** Never paste a private key into `.env` or the app. Restart ByeBike after editing the file:

```powershell
.\.venv\Scripts\python.exe app.py
```

Nonempty `.env` values override saved dashboard settings at startup. Existing system environment variables take precedence over `.env`. While the app runs, you can change cloud settings in the **Google Cloud Storage** panel. To use only the panel's saved settings on future starts, leave the corresponding `.env` fields blank. Set `BYEBIKE_GCS_ENABLED=false` to explicitly disable cloud uploads on startup.

### Optional: use an existing service account

For an unattended camera computer, use a dedicated identity rather than a personal account. If you already have a service-account JSON credential, grant that service account **Storage Object Creator** on the bucket and keep its file **outside this repository**. Set its path using forward slashes:

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=C:/Users/ganqi/keys/byebike-storage.json
```

Alternatively, enter this path in the dashboard's credentials field. When running on Google Cloud later, an attached service account can supply ADC without a key file. [How ADC finds credentials](https://cloud.google.com/docs/authentication/application-default-credentials)

## 4. Verify one upload

1. Open ByeBike and expand **Google Cloud Storage**. Check the bucket and that uploads are on.
2. Click **Try sample image** (or select it under camera connection). Draw a pedestrian area across the sample bicycle's wheels, save paths, and start **Live Monitor**.
3. A confirmed event should appear in the evidence log. Its label changes from **Uploading** to **GCS synced**. The sample is a statue image and tests the workflow only.
4. Open the `byebike-itss-lta` bucket in Cloud Console. The object is at `gs://byebike-itss-lta/byebike/YYYY-MM-DD/EVENT_ID.jpg` (UTC date). Evidence details also show this URI.

Only the **cyclist + bicycle crop** uploads. The full scene and JSON metadata remain local in `data/evidence/EVENT_ID/`. The bucket stays private; the dashboard shows the local copy.

## Failures and retries

Local evidence is saved before an upload is attempted. Upload failures show **Retry pending** in the log; open the evidence entry for its error. Fix authentication or connectivity and use **Retry**, or wait for automatic retries. Pending events survive app restarts and retain the bucket/prefix selected at capture time. Pausing cloud uploads pauses the retry queue, too.

Common fixes:

- **Credentials not found:** run `gcloud auth application-default login`, or check the JSON file path.
- **403 Forbidden:** grant the uploading identity Storage Object Creator on the target bucket; check project/quota permissions if mentioned in the error.
- **404 Not Found:** check the bucket name, then correct settings for new events. Old queued events retain their original destination in local metadata.
- **Still off after changing the dashboard:** check for `BYEBIKE_GCS_ENABLED=false` in `.env` or the system environment, then restart.

Existing local-only events are not uploaded retroactively when you enable GCS. Local evidence is not automatically deleted after upload.
