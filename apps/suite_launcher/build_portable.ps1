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
$updaterBuild = Join-Path $suiteRoot "build\updater"

if (Test-Path $portableRoot) {
    Remove-Item -Recurse -Force $portableRoot
}
if (Test-Path $launcherBuild) {
    Remove-Item -Recurse -Force $launcherBuild
}
if (Test-Path $updaterBuild) {
    Remove-Item -Recurse -Force $updaterBuild
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

# Small standalone helper that applies future in-place updates. Built as its
# own onefile exe (no Qt/venv dependency) and placed *next to* (not inside)
# the "LSPR Suite Launcher" folder, so it is never one of the files an update
# replaces. See apps/suite_launcher/updater/updater_main.py.
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Updater" `
    --distpath $portableRoot `
    --workpath $updaterBuild `
    --specpath $updaterBuild `
    (Join-Path $suiteRoot "apps\suite_launcher\updater\updater_main.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed for Updater.exe."
}

$copyTargets = @(
    "apps",
    "packages",
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

# --- Bundled Python runtime for launching the (non-frozen) apps -------------
#
# A plain `python -m venv .venv` is NOT relocatable: on Windows its
# Scripts\python.exe has no copy of the interpreter DLL or standard library -
# it depends on the base install living at the exact absolute path recorded
# in pyvenv.cfg's "home", which only exists on the machine that created the
# venv. Zip that up and hand it to another PC and every app launch fails
# silently and near-instantly, since python.exe itself can't start.
#
# Instead, copy the FULL base Python installation (interpreter + stdlib +
# DLLs) into the bundle and pip-install this project's dependencies directly
# into that copy's own site-packages, so the whole runtime is self-contained
# wherever the folder ends up. targets.py's resolve_python() already knows to
# look for python.exe at the root of .venv (a plain-install layout) as well
# as the usual venv Scripts\ location.
$baseHome = (& $venvPython -c "import sys; print(sys.base_prefix)").Trim()
if (-not (Test-Path $baseHome)) {
    throw "Could not resolve the base Python installation directory ($baseHome)."
}

$bundlePythonDir = Join-Path $bundleRoot ".venv"
if (Test-Path $bundlePythonDir) {
    Remove-Item -Recurse -Force $bundlePythonDir
}
Copy-Item $baseHome $bundlePythonDir -Recurse -Force

# Drop the base install's own site-packages (may carry unrelated global
# packages, especially on a local dev machine) - ensurepip + pip below
# repopulate it from scratch with exactly this project's dependencies.
$bundleSitePackages = Join-Path $bundlePythonDir "Lib\site-packages"
if (Test-Path $bundleSitePackages) {
    Remove-Item -Recurse -Force $bundleSitePackages
}

# Prune stdlib/toolchain pieces the suite never uses at runtime (PyQt6 is the
# GUI toolkit, not tkinter; nothing here compiles C extensions or needs the
# stdlib docs). Best-effort - a prune target not existing on a given Python
# build is not an error.
$pruneTargets = @(
    "Doc",
    "tcl",
    "include",
    "libs",
    "tools",
    "Lib\test",
    "Lib\idlelib",
    "Lib\tkinter",
    "DLLs\tcl86t.dll",
    "DLLs\tk86t.dll",
    "DLLs\_tkinter.pyd"
)
foreach ($relativePath in $pruneTargets) {
    $target = Join-Path $bundlePythonDir $relativePath
    if (Test-Path $target) {
        Remove-Item -Recurse -Force $target
    }
}

$bundlePython = Join-Path $bundlePythonDir "python.exe"
if (-not (Test-Path $bundlePython)) {
    throw "Copied Python runtime is missing python.exe at $bundlePython."
}

# site-packages was just deleted, which also removed pip itself - ensurepip
# (stdlib, always present) reinstalls it fresh before anything else can run.
& $bundlePython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) {
    throw "Failed to bootstrap pip in the bundled Python runtime."
}
& $bundlePython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in the bundled Python runtime."
}
& $bundlePython -m pip install -r (Join-Path $suiteRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install requirements.txt into the bundled Python runtime."
}
& $bundlePython -m pip install AMFTools
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not install AMFTools into the bundled Python runtime - M-Switch support will be unavailable in this build. Continuing without it."
}

Write-Host "Portable launcher bundle created at $bundleRoot"
