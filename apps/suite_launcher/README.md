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

If you want to use VS Code's "Run Python File" button, run
[`apps/suite_launcher/run.py`](run.py) instead of `app.py`.
