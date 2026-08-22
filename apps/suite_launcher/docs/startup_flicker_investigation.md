# Suite Launcher startup flicker — investigation writeup (2026-08)

## Symptom

When starting LSPR Suite, one or more small blank/white rectangular windows
briefly appeared and disappeared before the real "LSPR Suite" window settled
into view. Reported as "lot of small windows pop and close" during startup.

This took roughly six rounds of screen-recording analysis and five unrelated
(but individually legitimate) fixes before the real cause was found. This
doc exists so the next time something like this shows up — in the launcher
or any other app — it gets found in minutes via instrumentation instead of
hours via video.

## What was tried and ruled out

Each of these was a real, verified fix for a real thing. None of them were
the cause of the flicker. Listed in the order tried, with why each seemed
plausible at the time:

1. **`LaunchCard` built without a `parent` argument.** Confirmed via code
   read: the call site passed 9 positional args and never supplied the
   10th (`parent`), so every card was briefly a genuine parentless
   top-level widget until `grid.addWidget()` reparented it a few lines
   later. Fixed by passing `parent=self` explicitly. This was a real latent
   bug worth fixing — just not the one causing the reported symptom.

2. **White flash on first paint.** Confirmed via video: the window's
   client area painted solid white for ~3 frames before the dark
   stylesheet/gradient caught up — the classic "Windows erases a new
   native window's background before Qt's own paint pass runs" issue.
   Fixed by giving the window its own dark `QPalette` +
   `setAutoFillBackground(True)` so the very first native paint is already
   correct. This genuinely eliminated the white flash (confirmed on video),
   but the *separate* small-window flicker persisted.

3. **No explicit window size before `.show()`.** Theory: without
   `resize()`, the window only grows to fit the card grid *after* Windows
   has already started compositing it, producing several distinct
   in-between states. Matches a real, already-documented issue in
   `apps/LSPRi/eva/src/lspr_imaging_app/app.py`'s `main()` (see the comment
   above its `finish_startup()` about "two visibly distinct show states").
   Added `window.resize(900, 560)` before `show()`. Sound fix in principle;
   had zero measurable effect on this particular flicker.

4. **Repeated/overlapping launches.** The launcher had no single-instance
   protection at all — clicking Run multiple times (or double-clicking a
   shortcut repeatedly) queued up launches with no graceful handling.
   Added proper `QLockFile`-based single-instance locking (mirroring the
   existing pattern in `apps/sLSPR/acq/src/lspr_app/app.py`) plus a
   `QLocalServer`/`QLocalSocket` ping so a second launch just raises the
   existing window instead of building its own UI. This is a real, useful
   feature (the user explicitly wanted it) and was a reasonable suspect
   given the flicker's irregular, multiplying-across-videos frequency —
   but disabling this didn't stop the flicker either, and it's a legitimate
   keeper regardless.

5. **`Start Menu` shortcut refresh touching Windows shell/COM state.**
   `refresh_start_menu_shortcuts()` (added this same session, for the
   taskbar-identity fix below) is the only code that runs before any window
   exists and the only code that touches the app's icon via COM calls
   (`IShellLink`/`IPersistFile`). Temporarily disabled it entirely as an
   isolation test. Flicker persisted unchanged — ruled out definitively.

6. **`window.grab()` before `.show()`** to force a full paint into Qt's
   backing store while still hidden, so the first real paint after
   `.show()` is a fast blit instead of a live computation. Reasonable
   theory, zero effect.

Also investigated and ruled out along the way: VS Code's own "Stop
recording" tooltip (confirmed via a clean, readable frame — real, but
unrelated, artifact from the screen-recording tool itself, initially
mistaken for the same phenomenon before the user gave a precise on-screen
location that didn't match the tooltip's position).

## What actually worked: instrument, don't guess

Video analysis has a hard ceiling: this session's sandbox could not
reliably observe freshly-spawned windows via Win32 APIs (a test window
never became `EnumWindows`-visible even after 8 seconds, despite genuinely
running) and screen recordings only ever show *when* something happened,
never *why*. Six rounds of frame-by-frame pixel analysis (including
comparing the flickering window's icon against the real app icon,
pixel-for-pixel) established *that* something was flashing but never *why*.

The fix was ported directly from prior art in this exact codebase:
`StartupSuspiciousWidgetTracer` in `apps/sLSPR/acq/src/lspr_app/app.py`,
built for this exact recurring class of bug. A trimmed copy was added
temporarily to `suite_launcher/app.py`:

- Installed as a `QApplication` event filter, before any other startup
  work.
- Filters for `QWidget` instances where `obj.isWindow()` is true (i.e.
  Qt currently considers them a top-level window) receiving
  Show/Hide/Close/ShowToParent/Polish events.
- Flags ones that look accidental: no parent, no object name, an
  auto-generated (`qt_...`) object name, or no window title.
- Logs the widget's class/geometry/parent, and — critically — a full
  Python stack trace at the moment of the `Show` event.

Running the launcher once with this active (captured via
`subprocess.Popen(..., stdout=PIPE)`, no video needed) produced an exact
stack trace on the first try:

```
SUSPICIOUS_WIDGET | event=Show | class=QLabel | objectName='CardVersion' |
title='' | visible=True | geo=(55,-1695,640,480) | ...
  File ".../suite_launcher/app.py", line 1169, in __init__
    card = LaunchCard(
  File ".../suite_launcher/app.py", line 741, in __init__
    self.version_label.setVisible(bool(local_version))
```

`geo=(...,640,480)` is the tell: that's Qt's default top-level widget size,
not anything a version-number label would ever really have.

## Root cause

In `LaunchCard.__init__` (`apps/suite_launcher/src/suite_launcher/app.py`):

```python
self.version_label = QLabel(f"v{local_version}" if local_version else "")
self.version_label.setObjectName("CardVersion")
self.version_label.setWordWrap(True)
self.version_label.setVisible(bool(local_version))   # <- while still parentless
header_col.addWidget(self.version_label)
```

`setVisible()` was called while the label had no parent yet. On
Qt/Windows, calling a visibility method (`setVisible()`, `.show()`,
`.hide()`) on a still-parentless widget doesn't just set a property — Qt
briefly realizes it as an actual OS-level top-level window (default
geometry, genuinely composited by Windows), then immediately shrinks and
reparents it once it's added to a layout. One `CardVersion` label per app
card with a resolvable version number → one flash per card.

**Reordering `addWidget()` before `setVisible()` was tried first and did
NOT fix it.** The reason: `header_col`/`top_row` are bare
`QVBoxLayout()`/`QHBoxLayout()` objects that are not yet attached to any
real parent *widget* at that point in the constructor — they only become
part of `self`'s real widget tree several lines later, via
`layout.addLayout(top_row)` (where `layout = QVBoxLayout(self)` is the
card's own, actually-attached layout). `header_col.addWidget(...)`
associates the label with that pending layout, but does not give it a real
parent *widget* until the whole layout chain connects back to `self`. So
`setVisible()`, wherever it's placed relative to `header_col.addWidget()`,
still runs before the label has a real parent.

## Fix

Give the label its parent directly at construction, sidestepping the whole
layout-attachment-order question:

```python
self.version_label = QLabel(f"v{local_version}" if local_version else "", self)
```

## Verification

- Re-ran with the tracer active after the fix: **zero** suspicious widget
  events, across two separate runs (vs. firing on every run before).
- Full-clip frame-by-frame review of a fresh screen recording after the
  fix: clean single transition from editor to fully-rendered window, no
  white flash, no small-window flicker anywhere in the clip.
- `pytest tests/unit -k launcher` and a manual launch both still pass/run
  cleanly.

The temporary tracer was removed once it had done its job — it's not meant
to live in `suite_launcher/app.py` permanently, unlike sLSPR Acquisition's
copy, which stays because that app's startup is complex enough (async
hardware/data loading) to warrant a standing diagnostic.

## Takeaway for next time

If a small, otherwise-unexplained window flash shows up during any app's
startup again: reach for a widget-event tracer (`StartupSuspiciousWidgetTracer`
in `apps/sLSPR/acq/src/lspr_app/app.py`, or a trimmed copy per this doc)
*before* spending multiple rounds on screen-recording analysis. It finds
the exact offending line with a stack trace in one run; video analysis can
only ever narrow down *when*, and this codebase has already paid for that
lesson twice now.
