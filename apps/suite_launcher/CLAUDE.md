# CLAUDE.md - Suite Launcher

Loaded when working under `apps/suite_launcher/`. Root rules still apply.

## Pitfalls

- **A parentless widget that has `setVisible()`, `.show()`, or `.hide()` called on it can flash a phantom top-level window.** Pass the parent at construction (`QLabel(text, self)`). Do not rely on a layout that is not attached to a parent yet: `layout.addWidget(...)` alone does not fix it.
- **Diagnose with instrumentation before guessing from screen recordings.** Install a `QApplication` event filter that flags top-level Show, Hide, and Polish events on parentless widgets. Mirror `StartupSuspiciousWidgetTracer` in `apps/sLSPR/acq/src/lspr_app/app.py`.
- Investigation history, including the `LaunchCard` case: `docs/startup_flicker_investigation.md`.
