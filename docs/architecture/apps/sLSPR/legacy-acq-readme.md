# LSPR Acquisition

Desktop application for live LSPR spectroscopy acquisition, analysis, and experiment control.

## Features

- Live spectrometer acquisition with simulated fallback
- Peak tracking, smoothing, centroid, polynomial, and Gaussian analysis
- Experiment control window for pump and valve hardware
- HDF5 and CSV export for measurement data
- Compact debug log terminal and live status footer

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
- Measurement files and local settings are ignored by Git so the repository stays clean.
- The project name shown in the UI is `LSPR Acquisition`.
- Versioning is documented in `docs/versioning.md`.
- The native experiment-plan format is documented in `docs/experiment_plan_format.md`.
