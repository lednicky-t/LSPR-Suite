---
paths:
  - "apps/**/gui/**"
  - "apps/LSPRi/eva/src/**/panels/**"
  - "apps/suite_launcher/**"
---

# GUI rules

## Maintainer's UI principles (all apps)

- **A control does exactly what it says, every time.** No hidden exceptions (e.g. a tool silently switching the picked mode), and no quietly adapting a chosen value to the data (a size, mode or threshold means the same for every selection; if space runs out, shorten the content instead). A colour or icon keeps one meaning across the app, from one shared source; reuse icons users already know; follow established conventions (Photoshop Shift/Alt, right = bigger) where they exist. If an exception seems useful, ask first: a flagged exception is still read as a bug.
- **Undo instead of guards; live feedback must feel instant.** Prefer making an action undoable over size limits, confirmation dialogs or previews; add a live preview only when asked. Where there is live feedback, measure it and keep heavy work off the GUI thread; keep the old result on screen until the new one is ready. An action that does nothing or is refused says why in the status bar, never silently.

**Describe new screens and dialogs before coding.** This applies to new screens and dialogs only, not to cosmetic tweaks (icons, labels, colors, spacing, moving a widget). Write this first:

```text
Screen purpose:
Main user actions:
Layout:
- Left panel:
- Center panel:
- Right panel:
- Bottom/status area:
Important states:
Validation rules:
Error messages:
```

Keep layout code separate from business logic. Use the project's existing GUI patterns.

- **Startup popups fail closed.** Do not call `showPopup()` during widget construction unless an explicit ready flag is set. Default popup readiness to `False` and enable it only after startup wiring is complete. Use explicit startup-state propagation, not `getattr(..., True)` fallbacks, for combo-box popups.
- **Parentless widgets flash a phantom window.** Pass the parent at construction (`QLabel(text, self)`), not only through a layout that is not attached yet. Calling `setVisible()`, `.show()`, or `.hide()` on a parentless widget makes Qt briefly show it as a real top-level window. Details: `apps/suite_launcher/CLAUDE.md`.
- **Prefer directly callable widgets.** Use real `QAbstractButton` subclasses (`QPushButton`, `QToolButton`, `QCheckBox`) over a bare `QLabel` with a click handler. A real button's `.click()` fires the same signal a mouse click would, so tests need no screen coordinates. Pattern: `tests/integration/test_lspri_preferences_dialog.py` (a real `QApplication` built in-process, never `.exec()`).
- **Main window is split across files.** See "Where Code Lives" in the root `CLAUDE.md`.
