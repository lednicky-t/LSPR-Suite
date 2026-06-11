# Logging and Diagnostics Review

Scope:
- `LSPR-Suite/apps/suite_launcher`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app`

This review focuses on log-volume controls, runtime diagnostics, and the help-menu performance switches that affect GUI and file growth.

## Executive Summary

- The suite has two launcher-level log controls and one acquisition-app debug control that materially change log volume.
- The biggest log-growth sources are the always-on diagnostic export path, the session statistics snapshots when recording is active, and the many throttled INFO/DEBUG messages that are enabled by the Help menu debug switch.
- The most useful always-on diagnostics are the runtime drift probe, the session statistics snapshot machinery, and log throttling/deduplication.
- `trace_plot_controller.py` is a compatibility alias file only; it is legacy plumbing, not a standalone diagnostics tool.

## Toggle Inventory

### Suite Launcher

- `Quiet logs` on the `slspr_acq` card.
- Files: [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L88), [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L189), [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L357).
- Effect: turns on quiet diagnostics for acquisition launch, disables GUI terminal log forwarding, and reduces session stats to the minimal stability signals.
- Assessment: useful for long runs and routine operation; optional for debugging.

- `File info` on the `slspr_acq` card.
- Files: [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L89), [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L195), [apps/suite_launcher/src/suite_launcher/app.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py#L377).
- Effect: filters INFO-level diagnostics out of startup and session log files while keeping warnings and errors.
- Assessment: good as an advanced noise-reduction switch, but it overlaps with `Quiet logs` and is mainly useful for A/B comparisons.

### Acquisition App Help Menu

- `Debug mode`.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/chrome.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/chrome.py#L104), [apps/sLSPR/acq/src/lspr_app/gui/main_window.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py#L744), [apps/sLSPR/acq/src/lspr_app/gui/main_window.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py#L3702), [apps/sLSPR/acq/src/lspr_app/domain/processing.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/domain/processing.py#L17).
- Effect: enables slow-spectrum profiling and extra processing/plot/acquisition timing messages.
- Assessment: keep off by default. This is developer-only instrumentation and it clearly increases log volume.

- `Performance switches` submenu.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/chrome.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/chrome.py#L110), [apps/sLSPR/acq/src/lspr_app/gui/main_window.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py#L2538), [apps/sLSPR/acq/src/lspr_app/gui/main_window_lifecycle.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_lifecycle.py#L96).
- `Acquisition-state autosave`: persists acquisition state during UI and acquisition changes.
- `UI-state autosave`: persists window geometry and UI layout changes.
- `Log buffering`: batches log writes before rendering them.
- `GUI housekeeping`: runs deferred flushing and save tasks.
- `Metric plot`: enables the sensorgram metric line plot.
- Assessment: all four are operational switches, not debug-only features. `Metric plot` is the only one that is meaningfully optional for some workflows. The autosave and housekeeping switches should normally stay on.

### In-App Log Panel Controls

- `All`, `GUI`, `Devices` view buttons.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py#L65).
- Effect: changes which records are displayed in the terminal, not which records are generated.
- Assessment: useful for analysis, but not a log-volume control.

- `Follow`.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py#L97), [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py#L362).
- Effect: auto-scrolls the log terminal to newest entries.
- Assessment: UI convenience only.

- `Clear` and `Copy`.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py#L94).
- Effect: terminal only.
- Assessment: convenience only.

## Diagnostics and Statistics Tools

- `SessionDiagnosticsSnapshot` and `build_session_statistics_lines`.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/runtime_diagnostics.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/runtime_diagnostics.py#L384), [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py#L813).
- Content: scheduler timing, UI housekeeping, log buffer timing, deferred refresh timing, queue sizes, plot cache behavior, heatmap status, runtime drift summary, and many render metrics.
- Assessment: very useful for analysis and support. Keep it, but consider reducing what is exported automatically.

- Runtime drift probe.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/runtime_probe.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/runtime_probe.py#L160), [apps/sLSPR/acq/src/lspr_app/gui/main_window.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py#L1583).
- Effect: samples drift every 60 seconds with 12 samples retained.
- Assessment: low-overhead and valuable for long-run stability analysis. This is a good candidate to keep always on.

- Diagnostic export to JSONL.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py#L135), [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py#L206), [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py#L255), [apps/sLSPR/acq/src/lspr_app/diagnostics.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/diagnostics.py#L18).
- Effect: writes batched diagnostic events and periodic diagnostic snapshots to `perf_diagnostics_*.jsonl`.
- Assessment: useful for deep analysis, but it is a major source of file growth. This is the strongest candidate for an off-by-default or hidden advanced toggle.

- Session stats recording.
- Files: [apps/sLSPR/acq/src/lspr_app/gui/main_window.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py#L3243), [apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py#L817).
- Effect: records session stats snapshots to text files while the recording mode is active.
- Assessment: keep as an explicit on-demand capture tool. It is not appropriate as a permanent always-on logger.

## What Is Useful To Keep Always On

- Runtime drift probe.
- Session diagnostics snapshot generation in memory.
- Log throttling and duplicate collapse.
- Autosave of UI state and acquisition state.
- GUI housekeeping that batches background updates.

## What Should Stay Optional

- `Debug mode`.
- `File info`.
- `Metric plot` if the workflow is mostly heatmap-only.
- Diagnostic JSONL export.
- Session stats recording.
- `GUI` and `Devices` log filters, because they are presentation filters rather than operating modes.

## Likely Obsolete or Legacy

- `trace_plot_controller.py` is only a compatibility alias over `plot_controller.py`.
- File: [apps/sLSPR/acq/src/lspr_app/gui/trace_plot_controller.py](/C:/Users/Admin/Documents/GitHub/LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/trace_plot_controller.py#L1).
- Assessment: safe to treat as legacy shim. It should remain only if there are still external imports depending on the old name.

## Main Log-Size Drivers

- Startup log files are created on each launch via `startup_log_*.log`.
- Diagnostic export writes every batched event plus periodic snapshots.
- Session stats recording writes additional text snapshots while active.
- Debug mode increases the number of INFO/DEBUG timing messages from acquisition, processing, and plotting code.
- The launcher `File info` toggle only trims INFO lines, so it reduces size but does not address the larger export files.

## Practical Simplification Recommendation

- Keep one visible launcher-level switch for routine operation: a single "Quiet diagnostics" mode that also suppresses file INFO noise.
- Move the stricter file-side filter behind an advanced or hidden setting if the A/B test is still needed.
- Keep `Debug mode` only in the Help menu.
- Keep autosave and housekeeping enabled by default.
- Consider disabling diagnostic JSONL export by default, or gate it behind a separate explicit "export diagnostics" option.
- Consider renaming `File info` to something more explicit if it remains visible, because it is really a log-volume filter, not a user feature.

## Bundle Contents

- `LSPR-Suite/analysis/logging_diagnostics_report.md`
- `LSPR-Suite/apps/suite_launcher/src/suite_launcher/app.py`
- `LSPR-Suite/apps/suite_launcher/README.md`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/app.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/diagnostics.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/chrome.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_lifecycle.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_logging_ui.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/main_window_runtime.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/runtime_diagnostics.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/runtime_probe.py`
- `LSPR-Suite/apps/sLSPR/acq/src/lspr_app/gui/trace_plot_controller.py`
