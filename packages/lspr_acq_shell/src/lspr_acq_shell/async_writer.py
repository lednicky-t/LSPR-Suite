"""Generic background-thread, tag-queue writer: a `queue.Queue` feeds a
dedicated worker thread that periodically flushes accumulated work to an
underlying writer object, with graceful shutdown (drain-then-join) and an
error-callback escape hatch when the underlying writer fails.

Extracted from singleLSPR Acquisition's `AsyncHDF5MeasurementWriter`
(Phase 1, 2026-08-07). The queue/thread/flush-timing/close-draining/
save-copy-ordering mechanics were already app-agnostic; what was NOT generic
was the tag set and the concrete writer it drove - the original class
hardcoded `HDF5MeasurementWriter(...)` construction and spectrum-shaped tags
(`"append"`, `"metrics"`, ...) directly in its run loop. A literal move
would have relocated an sLSPR-specific class, not something LSPRimaging
acq's future cube writer could actually reuse - see the plan doc's §8/§9,
which already assumes "new tags (cube, roi_definitions) dispatched the same
way append/metrics already are."

Subclasses own both the tag set and the concrete writer via three hooks:

- `_open_writer()` -> the concrete writer object, opened/ready to receive
  calls. Called once, on the background thread, before the dispatch loop
  starts.
- `_apply(writer, tag, payload)` -> handle one dequeued item whose tag isn't
  one of this base's four structural tags (`"flush"`/`"close"`/
  `"save_copy"`/`"timeout"`, handled here). Immediate-effect tags should call
  straight through to `writer`; tags meant to batch until the next flush
  should accumulate into subclass-owned instance state instead.
- `_flush_pending(writer)` -> write out and clear any state `_apply`
  accumulated. Called before every `writer.flush()` - on an explicit
  `flush()`/`save_copy()` call, on `close()`, and on every periodic timeout
  tick, even ones where nothing was queued - a subclass with no batching can
  leave this a no-op.

The concrete writer object only needs `flush()`, `copy_into(dest_path)`, and
`close()` (see `WriterProtocol`) - `_open_writer`/`_apply` are free to call
whatever else it exposes.
"""
from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol

log = logging.getLogger(__name__)

_CLOSE_JOIN_TIMEOUT_S = 10.0


class WriterProtocol(Protocol):
    def flush(self) -> None: ...
    def copy_into(self, dest_path: Path) -> None: ...
    def close(self) -> None: ...


class AsyncTaggedWriter(ABC):
    def __init__(
        self,
        *,
        flush_interval_s: float = 2.0,
        on_error: Callable[[str], None] | None = None,
        label: str = "",
    ) -> None:
        self._flush_interval_s = max(float(flush_interval_s), 0.25)
        self._label = label
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop_event = threading.Event()
        self._closed = False
        self._state_lock = threading.Lock()
        # Plain callback, not a Qt signal - this module has no Qt dependency
        # by design, so marshaling onto the GUI thread is the caller's
        # responsibility. Called from this object's background writer
        # thread, not the caller's thread.
        self._on_error = on_error
        self._thread = threading.Thread(target=self._run, name="async-tagged-writer", daemon=True)
        self._thread.start()

    @abstractmethod
    def _open_writer(self) -> WriterProtocol: ...

    @abstractmethod
    def _apply(self, writer: WriterProtocol, tag: str, payload: object) -> None: ...

    @abstractmethod
    def _flush_pending(self, writer: WriterProtocol) -> None: ...

    def _open_error_message(self, exc: Exception) -> str:
        return f"Could not open the writer: {exc}"

    def _run_error_message(self, exc: Exception) -> str:
        return f"Writer stopped unexpectedly: {exc}"

    def _put(self, tag: str, payload: object) -> None:
        if self._closed:
            return
        self._queue.put((tag, payload))

    def flush(self) -> None:
        self._put("flush", None)

    def save_copy(self, dest_path: Path, on_done: Callable[[bool, str], None] | None = None) -> None:
        """Flush pending data, then copy the file to *dest_path*.

        Runs entirely inside the writer's own background thread, after the
        flush - so the copy only ever happens once every pending write has
        been drained and nothing else is touching the file at the same time.

        *on_done* is called from that same background thread, not the
        caller's - same contract as *on_error* in `__init__`.
        """
        if self._closed:
            if on_done is not None:
                on_done(False, "Writer is already closed.")
            return
        self._put("save_copy", (Path(dest_path), on_done))

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            self._queue.put(("close", None))
        self._thread.join(timeout=_CLOSE_JOIN_TIMEOUT_S)
        if self._thread.is_alive():
            log.warning(
                "Writer thread for %s did not stop within %.0fs of close(); "
                "it may still be flushing or stuck.",
                self._label,
                _CLOSE_JOIN_TIMEOUT_S,
            )

    def _notify_error(self, message: str) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(message)
        except Exception:
            log.exception("on_error callback for writer (%s) raised", self._label)

    def _run(self) -> None:
        try:
            writer = self._open_writer()
        except Exception as exc:
            log.exception("Failed to open writer for %s", self._label)
            self._closed = True
            self._notify_error(self._open_error_message(exc))
            return

        last_flush = monotonic()
        error_message: str | None = None
        try:
            while True:
                timeout = max(0.0, self._flush_interval_s - (monotonic() - last_flush))
                try:
                    tag, payload = self._queue.get(timeout=timeout)
                except queue.Empty:
                    tag, payload = "timeout", None

                if tag == "flush" or tag == "timeout":
                    self._flush_pending(writer)
                    writer.flush()
                    last_flush = monotonic()
                elif tag == "save_copy":
                    dest_path, on_done = payload
                    self._flush_pending(writer)
                    writer.flush()
                    last_flush = monotonic()
                    try:
                        writer.copy_into(dest_path)
                        if on_done is not None:
                            on_done(True, "")
                    except Exception as exc:
                        log.exception("Failed to save a copy of %s to %s", self._label, dest_path)
                        if on_done is not None:
                            on_done(False, str(exc))
                elif tag == "close":
                    self._flush_pending(writer)
                    writer.flush()
                    break
                else:
                    self._apply(writer, tag, payload)

                if self._stop_event.is_set() and self._queue.empty():
                    break
        except Exception as exc:
            log.exception("Writer for %s stopped due to an error", self._label)
            self._closed = True
            error_message = self._run_error_message(exc)
        finally:
            # Close before notifying: on_error may wake a caller that treats
            # "error fired" as "the writer is done" (e.g. cleaning up the
            # underlying file) - notifying first raced that caller against
            # this thread's own close() still being in flight.
            try:
                writer.close()
            except Exception:
                log.exception("Failed to cleanly close writer for %s", self._label)
            if error_message is not None:
                self._notify_error(error_message)
