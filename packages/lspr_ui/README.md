# lspr-ui

Shared Qt theme tokens, icon helpers, and application bootstrap helpers for the
LSPR Suite. Used by all four apps (`apps/sLSPR/acq`, `apps/sLSPR/eva`,
`apps/LSPRi/eva`, `apps/suite_launcher`) so styling and iconography stay
consistent instead of each app reinventing its own.

## What's here

- `theme.py` — theme tokens (`APP_THEME`, `BRIGHT_THEME`, `GRAY_DARK_THEME`),
  `apply_base_app_theme()` to wire a theme onto a `QApplication`, and a set of
  reusable stylesheet builders (buttons, toolbars, section headers).
- `icons.py` — `load_tabler_icon()` and friends for loading the suite's
  vendored icon set. **Read [`ICONS.md`](ICONS.md) before adding a new icon or
  an icon-library dependency** — there's a deliberate, measured reason icons
  are vendored as individual SVGs here instead of pulled from a
  full icon-library package.
- `ui_helpers.py` — small reusable widgets (`CompactWedgeSlider`,
  `DualHandleRangeSlider`) and helpers for common widget patterns
  (`make_compact_spinbox`, `make_info_button`, `make_window_button`, ...).
- `windows_taskbar.py` — `set_windows_app_user_model_id()` and the per-app
  `APP_ID_*` constants, so each app's windows group correctly under their own
  icon/name in the Windows taskbar instead of all showing up as "Python".

## Usage

```python
from PyQt6.QtWidgets import QApplication
from lspr_ui import apply_base_app_theme, load_tabler_icon, APP_THEME

app = QApplication([])
apply_base_app_theme(app, APP_THEME)

icon = load_tabler_icon("player-stop")
```

## Adding to this package

This package is for logic genuinely shared across apps — a new stylesheet
helper, a new vendored icon, a new reusable widget. App-specific GUI code
belongs in the app's own `gui/` package, not here.
