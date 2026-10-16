param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [string]$DataDir = '',
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    if (-not $Install) {
        throw 'Python environment missing. Run .\start.ps1 -Install (or .\dev.ps1 -Install) once.'
    }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python 3.12 and its Windows py launcher.' }
}
if ($Install) {
    & $pythonPath -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$appArguments = @('app.py', '--port', $Port)
if ($NoBrowser) { $appArguments += '--no-browser' }
if ($DataDir) { $appArguments += @('--data-dir', $DataDir) }
& $pythonPath @appArguments
if ($LASTEXITCODE -ne 0) { throw "ByeBike exited with code $LASTEXITCODE." }
