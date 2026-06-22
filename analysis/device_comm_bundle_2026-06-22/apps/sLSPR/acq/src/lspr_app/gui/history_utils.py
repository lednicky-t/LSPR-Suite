from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def trim_history_tail_in_place(history: list[T], max_len: int) -> None:
    if max_len <= 0:
        history.clear()
        return
    excess = len(history) - max_len
    if excess > 0:
        del history[:excess]
