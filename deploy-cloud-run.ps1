param(
    [string]$Project = "byebike-511808",
    [string]$Region = "us-central1",
    [string]$Bucket = "byebike-itss-lta",
    [string]$Prefix = "byebike",
    [string]$Service = "byebike"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($Project -notmatch '^[a-z][a-z0-9-]+$' -or $Region -notmatch '^[a-z0-9-]+$' -or $Service -notmatch '^[a-z][a-z0-9-]+$') {
    throw "Invalid project, region or service name."
}
if ($Bucket -notmatch '^[a-z0-9][a-z0-9._-]+$' -or $Prefix -notmatch '^[a-zA-Z0-9/_-]*$') {
    throw "Use a bucket name and a simple folder prefix (letters, numbers, slashes, _ or -)."
}
$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "${Region}-docker.pkg.dev/${Project}/byebike/app:${tag}"
$builder = "projects/$Project/serviceAccounts/byebike-build@$Project.iam.gserviceaccount.com"

# One-time resources and password setup are documented in docs/GCP_HOSTING.md.
& gcloud builds submit . --project=$Project --region=$Region --config=cloudbuild.yaml `
    --service-account=$builder --gcs-source-staging-dir="gs://$Project-cloudbuild-source/source" `
    --substitutions="_IMAGE=$image" --quiet
if ($LASTEXITCODE -ne 0) { throw "Container build failed; deployment was not changed." }

& gcloud run deploy $Service --image=$image --project=$Project --region=$Region `
    --service-account="byebike-run@$Project.iam.gserviceaccount.com" `
    --cpu=2 --memory=4Gi --concurrency=8 --min=0 --max=1 --max-instances=1 `
    --no-cpu-throttling --session-affinity --timeout=120 --port=8080 `
    --set-env-vars="BYEBIKE_HOSTED=true,BYEBIKE_GCS_PROJECT=$Project,BYEBIKE_GCS_BUCKET=$Bucket,BYEBIKE_GCS_PREFIX=$Prefix,XDG_RUNTIME_DIR=/tmp" `
    --set-secrets="BYEBIKE_APP_PASSWORD=byebike-app-password:latest" `
    --startup-probe="httpGet.path=/healthz,httpGet.port=8080,initialDelaySeconds=0,periodSeconds=5,timeoutSeconds=3,failureThreshold=30" `
    --allow-unauthenticated --quiet
if ($LASTEXITCODE -ne 0) { throw "Cloud Run deployment failed. Check the error above." }
& gcloud run services describe $Service --project=$Project --region=$Region --format="value(status.url)"
