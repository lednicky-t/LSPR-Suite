# LSPR Acquisition

Desktop application for live LSPR spectroscopy acquisition, analysis, and experiment control.

## Features

- Live spectrometer acquisition with simulated fallback
- Peak tracking, smoothing, centroid, polynomial, and Gaussian analysis
- Experiment control window for pump and valve hardware
- HDF5 and CSV export for measurement data
- Compact debug log terminal and live status footer
- Optional AMF M-Switch control when the vendor `AMFTools` package is installed
- Launcher modes that can skip device discovery or open only the experiment-control editor

## Repository layout

- `src/lspr_app/` - application package
- `docs/` - setup and hardware notes
- `drivers/` - controller and instrument notes
- `run.ps1` - helper launcher for local Windows runs

## Quick start

1. Create and activate a virtual environment.
2. Install the project in editable mode:

```powershell
python -m pip install -e .
```

3. Run the application:

```powershell
lspr-acquisition
```

If you prefer the package entry point:

```powershell
python -m lspr_app.app
```

## Notes

- The application uses a simulated spectrometer automatically when no Ocean Insight backend is available.
- The suite launcher can start the app in three profiles:
  - `Full`: current behavior, including startup hardware discovery and experiment-control auto-connect.
  - `Simulation`: skips startup device discovery and runs the acquisition UI in simulation mode.
- `Control editor`: opens the experiment-control editor without the runtime transport controls and without startup device discovery.
- The suite launcher also has a `File info` toggle for the acquisition card; when off, INFO-level diagnostics are filtered out of the startup/session log file to make A/B tests stricter.
- Measurement files and local settings are ignored by Git so the repository stays clean.
- The project name shown in the UI is `LSPR Acquisition`.
- Versioning is documented in `docs/versioning.md`.
- The native experiment-control plan format is documented in `docs/experiment_plan_format.md`.
- M-Switch control requires the optional `AMFTools` package; without it, the HW testbench and experiment-control M-Switch actions are unavailable.

