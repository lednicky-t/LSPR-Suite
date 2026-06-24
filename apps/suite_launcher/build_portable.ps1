$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$suiteRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$venvPython = Join-Path $suiteRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Missing virtual environment Python at $venvPython. Create the repo .venv first."
}

& $venvPython -m pip show pyinstaller | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install PyInstaller
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller installation failed."
    }
}

$portableRoot = Join-Path $suiteRoot "dist\LSPR-Suite-Portable"
$bundleRoot = Join-Path $portableRoot "LSPR Suite Launcher"
$launcherBuild = Join-Path $suiteRoot "build\suite_launcher"

if (Test-Path $portableRoot) {
    Remove-Item -Recurse -Force $portableRoot
}
if (Test-Path $launcherBuild) {
    Remove-Item -Recurse -Force $launcherBuild
}

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "LSPR Suite Launcher" `
    --distpath $portableRoot `
    --workpath $launcherBuild `
    --specpath $launcherBuild `
    --paths (Join-Path $suiteRoot "apps\suite_launcher\src") `
    --paths (Join-Path $suiteRoot "packages\lspr_ui\src") `
    --paths (Join-Path $suiteRoot "packages\lspr_core\src") `
    --paths (Join-Path $suiteRoot "packages\lspr_io\src") `
    (Join-Path $suiteRoot "apps\suite_launcher\run.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$copyTargets = @(
    "apps",
    "packages",
    ".venv",
    "requirements.txt",
    "README.md"
)

$null = New-Item -ItemType Directory -Path $bundleRoot -Force

foreach ($item in $copyTargets) {
    $source = Join-Path $suiteRoot $item
    if (-not (Test-Path $source)) {
        continue
    }
    $destination = Join-Path $bundleRoot $item
    if (Test-Path $destination) {
        Remove-Item -Recurse -Force $destination
    }
    Copy-Item $source $destination -Recurse -Force
}

Write-Host "Portable launcher bundle created at $bundleRoot"
