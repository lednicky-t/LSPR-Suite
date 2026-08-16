# LSPR Suite — Installation Guide

This guide is for the **portable build** of the LSPR Suite: a folder someone
handed you (or you downloaded) that already contains everything needed to run
the software. You do **not** need to install Python, Git, or anything else.

## 1. What you have

A folder named **"LSPR Suite Launcher"**. Inside it you'll find:

- `LSPR Suite Launcher.exe` — the program you actually run
- `Updater.exe` — a small helper the launcher uses when installing updates
  (you never run this yourself)
- `.venv` — a complete, self-contained copy of Python with everything the
  apps need already installed. Don't move or delete it; the apps won't start
  without it.
- `apps`, `packages` — the application source code
- `README.md` — developer-oriented notes about building from source (you can
  ignore this one; it's not written for a portable install)

## 2. System requirements

- Windows 10 or 11, 64-bit
- No admin rights required for normal use
- A few hundred MB of free disk space

## 3. Installing

There is no installer to run. Just:

1. Copy the **entire "LSPR Suite Launcher" folder** to wherever you want it
   to live — your Desktop, Documents, an external drive, etc.
2. Keep everything inside that folder together. Don't pull `LSPR Suite
   Launcher.exe` out on its own; it needs the rest of the folder next to it.
3. Avoid installing it inside `C:\Program Files\` unless you know the folder
   will stay writable — the built-in updater (see §7) replaces files inside
   this folder when you update, which needs write access.

## 4. First launch

Double-click **`LSPR Suite Launcher.exe`** inside the folder.

### "Windows protected your PC" warning

Because this build isn't digitally code-signed, Windows SmartScreen may show
a blue warning screen the first time you run it. This is expected — it's not
a sign of a virus, it just means the file hasn't built up a reputation with
Microsoft yet. To proceed:

1. Click **"More info"**
2. Click **"Run anyway"**

If your antivirus quarantines or deletes the `.exe` instead of just warning
you, you'll need to restore it from quarantine and add an exclusion for the
folder — ask your IT contact if you're on a managed lab computer.

## 5. Using the launcher

The launcher window shows four app cards:

- **singleLSPR acquisition** — run this to record measurements
- **singleLSPR evaluation** — analyze recorded singleLSPR sessions
- **LSPRimaging evaluation** — analyze LSPR imaging datasets
- **LSPRimaging acquisition** — reserved for future work, not available yet

Click **Launch** on a card to start that app. While it's running, the button
becomes **Kill**, which lets you force-stop it from the launcher if needed.

### Acquisition profiles

The singleLSPR acquisition card has an inline mode selector:

- **Full** — looks for real connected hardware and connects automatically
- **Simulation** — skips hardware discovery entirely and runs against a
  simulated spectrometer, useful for trying the software without any
  instrument connected
- **Control editor** — opens just the experiment-control plan editor, with
  no live acquisition controls

If you don't have a spectrometer connected yet, start with **Simulation** to
confirm the software runs correctly before troubleshooting hardware.

## 6. Optional hardware: AMF M-Switch

If your setup includes an AMF M-Switch valve, its control library
(`AMFTools`) is normally pre-installed into this bundle's Python runtime
already. If the M-Switch controls appear disabled in the acquisition app,
that install may have failed silently during the build — see the
troubleshooting section below.

Spectrometer USB driver setup (for Ocean Insight / Ocean Optics hardware) is
a one-time, per-computer step that's independent of this software bundle. If
your lab computer has never talked to the spectrometer before, that driver
setup still needs to happen — ask the person who normally sets up the
instrument for this PC, or use **Simulation** mode in the meantime.

## 7. Where your data and settings live

- **Measurement files** you save (HDF5 sessions, experiment-control plans,
  exported data) go wherever *you* choose in the Save dialog each time —
  nothing is written automatically to a fixed folder.
- **App settings** (window layout, last-used options, your user profile) are
  stored outside this folder entirely, under your Windows user profile at
  `%LOCALAPPDATA%\lspr-suite\`. This means settings survive even if you
  delete and re-copy the "LSPR Suite Launcher" folder, and multiple people
  sharing one PC each get their own settings.

## 8. Updating

The launcher can check whether a newer version is available. Click **Check
for Updates** in the launcher's settings area. If an update is found, it
downloads the new portable bundle and swaps it in for you automatically —
your settings (§7) are unaffected since they live outside this folder.

## 9. Uninstalling

Just delete the "LSPR Suite Launcher" folder. There's nothing else to clean
up — no registry entries, no separately-installed services. If you also want
to remove your saved settings, delete `%LOCALAPPDATA%\lspr-suite\` as well.

## 10. Troubleshooting

- **Nothing happens when I double-click the exe** — make sure you didn't
  move `LSPR Suite Launcher.exe` out of its folder; it needs the `.venv` and
  `apps` folders next to it.
- **An app card fails to launch** — try **Simulation** mode first (singleLSPR
  acquisition) to rule out a hardware/driver issue before assuming the
  software itself is broken.
- **M-Switch controls are greyed out** — `AMFTools` failed to install during
  the build (it's a best-effort install, not required for the rest of the
  suite to work). This needs a rebuild of the portable bundle with a working
  internet connection at build time; it isn't something you can fix from
  inside the finished portable folder.
- **Still stuck** — check the app's own diagnostics panel, or reach out to
  whoever provided you this build with the exact error message you see.
