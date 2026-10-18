"""`ImageRenderer`'s read-ahead reader threads (2026-10-14, testing7/8 analysis).

Read-ahead used to run on the render thread, so every render request that arrived during a ~30 ms plane read
waited for it. These tests pin the replacement: planes are read on separate reader threads, in parallel; the
render thread is never behind a read of some other plane; a render that needs a plane being read at that moment
waits for that read instead of decoding it twice; readers start on demand, leave when idle and are joined by `stop`.

No Qt application and no files: `load_plane` is a fake. Waits use events with generous timeouts (nothing here
depends on a short sleep being long enough).
"""

from __future__ import annotations

import sys
import threading
import time
import unittest

import numpy as np
from PyQt6.QtCore import Qt

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image.render import ImageRenderer, RenderRequest
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

WAIT = 5.0
PLANE = np.full((6, 8), 1000.0, dtype=np.float32)


def _request(cube: int, wavelength: float, serial: int = 1) -> "RenderRequest":
    return RenderRequest(
        cube_index=cube, wavelength_nm=wavelength, geometry=GeometrySettings(), background=BackgroundSettings(),
        authored_mask=None, mask_warp_affine=None, rois=(), detection=None, serial=serial,
    )


def _wait_until(condition, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class _ReadersCase(unittest.TestCase):
    def make(self, load_plane, **kwargs) -> "ImageRenderer":
        renderer = ImageRenderer(load_plane, **kwargs)
        self.addCleanup(renderer.stop)
        return renderer


class ParallelReadTests(_ReadersCase):
    def test_two_readers_read_two_planes_at_the_same_time(self) -> None:
        barrier = threading.Barrier(2, timeout=3.0)
        passed: list[int] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            barrier.wait()  # only returns when BOTH reads are inside `load` together
            passed.append(cube)
            return PLANE

        renderer = self.make(load)
        renderer.prefetch([(1, 500.0), (2, 500.0)], quiet_seconds=0.0)
        self.assertTrue(_wait_until(lambda: len(passed) == 2), "the two reads never overlapped")

    def test_a_plane_already_being_read_is_not_read_twice(self) -> None:
        gate, started = threading.Event(), threading.Event()
        calls: list[tuple[int, float]] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            calls.append((cube, wavelength))
            started.set()
            gate.wait(WAIT)
            return PLANE

        renderer = self.make(load)
        renderer.prefetch([(1, 500.0), (1, 500.0)], quiet_seconds=0.0)
        self.assertTrue(started.wait(WAIT))
        time.sleep(0.15)  # the second reader had every chance to pick up the duplicate
        self.assertEqual(calls, [(1, 500.0)])
        gate.set()

    def test_a_missing_plane_does_not_kill_a_reader(self) -> None:
        loaded: list[int] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            loaded.append(cube)
            if cube == 99:
                raise KeyError("missing plane")
            return PLANE

        renderer = self.make(load, readers=1)
        renderer.prefetch([(99, 500.0), (2, 500.0)], quiet_seconds=0.0)
        self.assertTrue(_wait_until(lambda: loaded == [99, 2]))


class RenderThreadIsNotBehindReadsTests(_ReadersCase):
    def _watch(self, renderer: "ImageRenderer") -> tuple[list, threading.Event]:
        results: list = []
        got = threading.Event()

        def on_rendered(result) -> None:
            results.append(result)
            got.set()

        renderer.rendered.connect(on_rendered, Qt.ConnectionType.DirectConnection)  # runs on the render thread
        return results, got

    def test_a_render_of_another_plane_is_not_held_up_by_a_slow_read_ahead(self) -> None:
        gate, started = threading.Event(), threading.Event()

        def load(cube: int, wavelength: float) -> np.ndarray:
            if cube == 1:  # the read-ahead plane: stuck until the test lets it go
                started.set()
                gate.wait(WAIT)
            return PLANE

        renderer = self.make(load)
        results, got = self._watch(renderer)
        renderer.prefetch([(1, 500.0)], quiet_seconds=0.0)
        self.assertTrue(started.wait(WAIT))
        renderer.submit(_request(2, 600.0))
        self.assertTrue(got.wait(WAIT), "the render thread was held up by a read-ahead of another plane")
        self.assertIsNone(results[0].error)
        self.assertFalse(results[0].request.submitted_while_prefetching)
        gate.set()

    def test_a_render_of_the_plane_being_read_waits_for_that_read_and_does_not_decode_it_again(self) -> None:
        gate, started = threading.Event(), threading.Event()
        calls: list[str] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            first = not calls
            calls.append(threading.current_thread().name)
            if first:
                started.set()
                gate.wait(WAIT)
            return PLANE

        renderer = self.make(load)
        results, got = self._watch(renderer)
        renderer.prefetch([(1, 500.0)], quiet_seconds=0.0)
        self.assertTrue(started.wait(WAIT))
        renderer.submit(_request(1, 500.0))
        self.assertFalse(got.wait(0.3), "rendered before the read of its plane had finished")
        self.assertEqual(len(calls), 1)  # still only the reader's decode
        gate.set()
        self.assertTrue(got.wait(WAIT))
        self.assertTrue(calls[0].startswith("ImageReader"))
        self.assertEqual(calls[1], "ImageRenderer")  # the render's own call: a plane-cache hit in the app
        self.assertTrue(results[0].request.submitted_while_prefetching)
        self.assertIsNone(results[0].error)


class ReaderLifecycleTests(_ReadersCase):
    def test_readers_start_on_demand_leave_when_idle_and_come_back(self) -> None:
        loaded: list[int] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            loaded.append(cube)
            return PLANE

        renderer = self.make(load)
        renderer.READER_IDLE_SECONDS = 0.2
        self.assertEqual(renderer._reader_threads, [])  # nothing started by construction
        renderer.prefetch([(1, 500.0)], quiet_seconds=0.0)
        self.assertTrue(_wait_until(lambda: loaded == [1]))
        started_threads = list(renderer._reader_threads)
        self.assertTrue(started_threads)
        self.assertTrue(_wait_until(lambda: not any(t.is_alive() for t in started_threads)), "an idle reader did not leave")
        renderer.prefetch([(2, 500.0)], quiet_seconds=0.0)
        self.assertTrue(_wait_until(lambda: loaded == [1, 2]), "no reader came back for a new list")

    def test_stop_joins_the_readers_and_later_lists_are_ignored(self) -> None:
        loaded: list[int] = []

        def load(cube: int, wavelength: float) -> np.ndarray:
            loaded.append(cube)
            return PLANE

        renderer = ImageRenderer(load)
        renderer.prefetch([(1, 500.0)], quiet_seconds=0.0)
        self.assertTrue(_wait_until(lambda: loaded == [1]))
        threads = list(renderer._reader_threads)
        renderer.stop()
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertFalse(renderer._thread.is_alive())
        renderer.prefetch([(2, 500.0)], quiet_seconds=0.0)  # after stop: no new thread, no read
        time.sleep(0.1)
        self.assertEqual(loaded, [1])

    def test_the_quiet_period_still_holds_back_the_first_read(self) -> None:
        loaded: list[int] = []
        renderer = self.make(lambda cube, wavelength: loaded.append(cube) or PLANE)
        started = time.monotonic()
        renderer.prefetch([(1, 500.0)], quiet_seconds=0.3)
        time.sleep(0.1)
        self.assertEqual(loaded, [])
        self.assertTrue(_wait_until(lambda: loaded == [1]))
        self.assertGreaterEqual(time.monotonic() - started, 0.3)


if __name__ == "__main__":
    unittest.main()
