# LSPR Suite Launcher

This is the suite-level startup screen for the LSPR ecosystem.

It shows the four app choices:

- singleLSPR acquisition
- singleLSPR evaluation
- LSPRimaging acquisition
- LSPRimaging evaluation

The launcher prefers the suite workspace copies when they exist, and falls back
to the legacy project locations during migration.

It also remembers the last app you launched and automatically opens it again
after a short delay on the next start.

For the singleLSPR acquisition app, the mode selector now lives inside the
acquisition card itself as a clickable inline label. It cycles through:

- `Full` keeps the current hardware discovery and auto-connect startup flow.
- `Simulation` skips startup device lookup and starts the acquisition UI in simulation mode.
- `Control editor` opens the experiment-control editor without the runtime transport controls.

The same acquisition card also has a visible `Quiet logs` toggle. When it is
on, the launcher starts singleLSPR acquisition with quiet diagnostics enabled:

- the GUI terminal log bridge stays off
- only warning-and-above output is kept in the launcher window
- the saved session statistics are reduced to the minimal stability signals

There is also a separate `File info` toggle for a stricter A/B test. When it is
off, INFO-level diagnostics are filtered out of the acquisition app's startup
and session log file, while warnings and errors still remain.

The launch button on each card now changes state:

- `Launch` starts the app.
- `Stop launch` cancels the auto-launch countdown for the remembered app.
- `Kill` stops a running app launched from the suite.

If you want to use VS Code's "Run Python File" button, run
[`apps/suite_launcher/run.py`](run.py) instead of `app.py`.

On Windows, make sure the interpreter behind your venv is a normal Python
install. If `python` points to the Inkscape bundle, recreate the venv with your
system Python before launching the suite.
