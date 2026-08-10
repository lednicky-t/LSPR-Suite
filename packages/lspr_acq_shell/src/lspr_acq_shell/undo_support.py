"""Shared app-wide undo/redo support, built on Qt's QUndoStack/QUndoCommand.

Moved verbatim from sLSPR acq's `gui/undo_support.py` (Phase 2, Tier 3a - full
experiment-control panel consolidation, 2026-08-09) - zero app coupling to begin
with (pure PyQt6 + stdlib), so this is a straight relocation, not a
generalization.

The whole app shares a single QUndoStack (owned by MainWindow) so Ctrl+Z always
undoes "the last thing I changed," anywhere in the app - not a separate history
per panel.

Every undoable action in this app is a snapshot: "the whole relevant piece of
state before" and "the whole relevant piece of state after," applied via a
single ``apply(value)`` callback. This is deliberately simpler than per-field
diffing - see SnapshotCommand's docstring for why.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Generic, TypeVar

from PyQt6.QtGui import QUndoCommand, QUndoStack

T = TypeVar("T")

# Generous enough to cover a real editing session without letting plan/settings
# snapshots accumulate unbounded in memory. User-configurable via Preferences
# ("Undo history size"), which calls QUndoStack.setUndoLimit() with the saved value.
DEFAULT_UNDO_HISTORY_SIZE = 50


class SnapshotCommand(Generic[T], QUndoCommand):
    """A generic undo command: apply *before* on undo, *after* on redo.

    Most edits in this app (a plan-table cell, a paste, a row move, a settings
    field) ripple into more than just the one value the user touched - e.g.
    editing one step's duration recomputes every other step's start/end time.
    Snapshotting the whole relevant state (the plan's step list, a settings
    dataclass, ...) before and after is the only way to guarantee undo
    restores *exactly* the prior state, rather than a partial-field diff that
    could miss a ripple effect and leave things inconsistent. The snapshots
    involved are small (short lists of small dataclasses), so this costs
    nothing worth optimizing for.

    *apply* is called with a deep copy of *before* (on undo) or *after* (on
    redo), so the command owns its own copies independent of whatever the
    caller does with the original objects afterward.
    """

    def __init__(self, description: str, before: T, after: T, apply: Callable[[T], None]) -> None:
        super().__init__(description)
        self._before = deepcopy(before)
        self._after = deepcopy(after)
        self._apply = apply

    def redo(self) -> None:  # noqa: D102 - Qt override
        self._apply(deepcopy(self._after))

    def undo(self) -> None:  # noqa: D102 - Qt override
        self._apply(deepcopy(self._before))


def push_snapshot(
    stack: QUndoStack | None,
    description: str,
    before: T,
    after: T,
    apply: Callable[[T], None],
) -> None:
    """Push a SnapshotCommand onto *stack*, or no-op if there's no real change.

    If *stack* is None (e.g. a widget constructed standalone in a test,
    without the app's shared undo stack), *apply* is not called here - the
    caller is expected to have already applied the change directly, the same
    way code worked before undo support existed.
    """
    if stack is None or before == after:
        return
    stack.push(SnapshotCommand(description, before, after, apply))
