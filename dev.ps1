param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [string]$DataDir = (Join-Path $PSScriptRoot 'data\dev'),
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
if ($env:K_SERVICE) {
    throw 'dev.ps1 is for your local computer. Use deploy-cloud-run.ps1 for Cloud Run.'
}

# Override .env and inherited hosting settings only for this development session.
# The original values are restored when the server exits, including Ctrl+C.
$developmentEnvironment = @{
    BYEBIKE_HOSTED = 'false'
    BYEBIKE_GCS_ENABLED = 'false'
}
$originalEnvironment = @{}
foreach ($name in $developmentEnvironment.Keys) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    foreach ($name in $developmentEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $developmentEnvironment[$name], 'Process')
    }
    Write-Host "ByeBike development: http://127.0.0.1:$Port"
    Write-Host "Local data: $DataDir"
    Write-Host 'GCS uploads start disabled. Press Ctrl+C to stop; restart after Python changes.'
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port -NoBrowser:$NoBrowser -DataDir $DataDir -Install:$Install
}
finally {
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
}
