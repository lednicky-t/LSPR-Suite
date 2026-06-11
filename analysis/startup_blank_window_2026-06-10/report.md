# Startup blank-window analysis

## What the current trace shows

- At startup, the only visible top-level widgets reported were the `MainWindow` and the `StartupSplash`.
- The main window reached `showEvent` quickly, then painted its container and plot widgets before data arrived.
- `first data render on trace` happened at about `+150 ms`.
- `first data render on spectrum` happened at about `+3193 ms`.
- The splash was closed only after the first spectrum render.

## Interpretation

The blank window is most likely the main window shell being exposed before the first populated spectrum render.

The opacity gate added in `app.py` is not meant to prove the root cause by itself. It is an isolation step:

- If the blank window disappears, it was the main window becoming visible too early.
- If a blank window still appears, then the visible object is likely another top-level widget or an OS-level frame, and the next step is to trace that specific window with a screenshot or per-widget visibility log.

## Files changed for this investigation

- `apps/sLSPR/acq/src/lspr_app/app.py`
- `apps/sLSPR/acq/src/lspr_app/gui/main_window.py`
- `apps/sLSPR/acq/src/lspr_app/gui/main_window_layout.py`
- `apps/sLSPR/acq/src/lspr_app/gui/acquisition_controller.py`

## Suggested next checks

1. Run the app with the new opacity gate and confirm whether the blank frame still appears.
2. If it still appears, capture a screenshot during the gap and compare it with the top-level widget trace in this bundle.
3. If the issue remains, instrument `QApplication.topLevelWidgets()` earlier in startup and log any window that becomes visible before the first spectrum render.
