"""Every thread or process pool, and every read of the core count, in the apps and packages is accounted for.

**Why this test exists.** The rule is: *no job works out its own worker count*. A parallel job asks `lspr_core.perf`
(`get_runtime().begin(...)` for a big job: it gets a tuned count **and a fair share of the cores** while other jobs run; or
`get_runtime().pool_threads(cap)` for a small fixed pool), so that two jobs at once share the cores instead of each taking all of them
(`docs/performance_tuning.md`, "The layers"). A rule that lives only in a document is forgotten by the next person (or the next agent) who
writes a quick `ThreadPoolExecutor(os.cpu_count())`. This test reads every source file (not comments or docstrings: it parses the code)
and fails when a new pool or core-count read appears that is not registered below.

**If this test fails for your new code**: do not just add a line to the table. First make the job use `lspr_core.perf` (the recipe is
in `docs/performance_tuning.md`), then register the file here with the way it does so. The table says *how* each file gets its count,
and the test checks that the file really does what the table says.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

POOL_CALLS = {"ThreadPoolExecutor", "ProcessPoolExecutor"}
COUNT_CALLS = {"cpu_count", "process_cpu_count"}

# How a file that creates a pool gets its worker count.
TUNED = "tuned"  # a big job: `get_runtime().begin(...)` (tuned count, fair share of the cores, gives the share back)
SMALL = "small"  # a small fixed pool that is not worth tuning: `get_runtime().pool_threads(cap)`
SIZE_FROM_CALLER = "size_from_caller"  # infrastructure: the size is a parameter, worked out by a caller that is itself registered
THE_PROBE = "the_probe"  # the one place that reads the machine

REGISTERED: dict[str, tuple[str, str]] = {
    "apps/LSPRi/eva/src/lspr_imaging_app/dataset/io.py": (TUNED, "OME-Zarr export (`export.ome_zarr`)"),
    "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan.py": (TUNED, "analysis scan (`analysis.scan`)"),
    "apps/LSPRi/eva/src/lspr_imaging_app/analysis/trace_pool.py": (TUNED, "Sensorgram fits (`sensorgram.fits`)"),
    "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask/feature_map_run.py": (TUNED, "Feature map (`mask.feature_map.*`)"),
    "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask/transient_scan.py": (TUNED, "transient scan (`mask.scan.screen`)"),
    "apps/LSPRi/eva/src/lspr_imaging_app/storage/session.py": (SMALL, "session mask PNG reads: hundreds of tiny files"),
    "apps/LSPRi/eva/src/lspr_imaging_app/storage/session_carry.py": (SMALL, "session carry: warping a session's masks"),
    "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan_pool.py": (
        SIZE_FROM_CALLER, "warm pool of the analysis scan: `scan_task` passes the size from `get_runtime().plan()`; `scan.py` from `begin()`"
    ),
    "packages/lspr_core/src/lspr_core/perf/system_profile.py": (THE_PROBE, "`probe_system()`: the one reader of the core count"),
}

# What the file's own source must contain for its registered kind (the table says how; the test checks that it is true).
REQUIRED = {
    TUNED: ("get_runtime", ".begin("),
    SMALL: ("pool_threads(",),
    SIZE_FROM_CALLER: (),
    THE_PROBE: (),
}


def _source_files() -> list[Path]:
    roots = [REPO_ROOT / "apps", REPO_ROOT / "packages"]
    found: list[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            parts = path.relative_to(REPO_ROOT).parts
            if "src" in parts and "__pycache__" not in parts and ".venv" not in parts and "site-packages" not in parts:
                found.append(path)
    return sorted(found)


def _called_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _finds(path: Path) -> list[tuple[int, str]]:
    """(line, name) of every pool created and every core count read in the code of ``path``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # not this test's business: the file's own tests say so
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name in POOL_CALLS or name in COUNT_CALLS:
                hits.append((node.lineno, name))
            elif name == "Pool" and isinstance(node.func, ast.Attribute):  # multiprocessing.Pool / ctx.Pool, not an unrelated `Pool` class
                hits.append((node.lineno, "Pool"))
    return hits


class ParallelWorkFollowsPerfRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.found = {path.relative_to(REPO_ROOT).as_posix(): _finds(path) for path in _source_files()}
        cls.found = {path: hits for path, hits in cls.found.items() if hits}

    def test_every_pool_and_core_count_is_registered(self) -> None:
        unregistered = {path: hits for path, hits in self.found.items() if path not in REGISTERED}
        self.assertEqual(
            unregistered, {},
            "A thread / process pool or a core-count read is new here. A job must not work out its own worker count: ask `lspr_core.perf` "
            "(`get_runtime().begin(...)` for a big job, `get_runtime().pool_threads(cap)` for a small fixed pool; recipe: "
            "docs/performance_tuning.md), then register the file in REGISTERED above with how it does so.",
        )

    def test_the_table_has_no_stale_entries(self) -> None:
        stale = sorted(path for path in REGISTERED if path not in self.found)
        self.assertEqual(stale, [], "these files no longer create a pool or read the core count: remove them from REGISTERED")

    def test_each_registered_file_really_does_what_the_table_says(self) -> None:
        for path, (kind, reason) in REGISTERED.items():
            source = (REPO_ROOT / path).read_text(encoding="utf-8")
            for needle in REQUIRED[kind]:
                self.assertIn(needle, source, f"{path} is registered as '{kind}' ({reason}) but never calls {needle!r}")

    def test_the_core_count_is_read_in_one_place_only(self) -> None:
        readers = sorted(path for path, hits in self.found.items() if any(name in COUNT_CALLS for _, name in hits))
        self.assertEqual(readers, ["packages/lspr_core/src/lspr_core/perf/system_profile.py"],
                         "`os.cpu_count()` ignores a CPU-affinity limit and the user's 'cores kept free': use `get_runtime().pool_threads(...)`")

    def test_a_pool_of_a_tuned_job_is_never_sized_by_a_literal(self) -> None:
        """`max_workers=4` in a registered 'tuned' file would be a hard-coded count again."""
        for path, (kind, _) in REGISTERED.items():
            if kind != TUNED:
                continue
            tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _called_name(node) in POOL_CALLS:
                    sizes = [kw.value for kw in node.keywords if kw.arg == "max_workers"] + node.args[:1]
                    for size in sizes:
                        self.assertNotIsInstance(size, ast.Constant, f"{path}:{node.lineno} sizes a pool with a literal number")


if __name__ == "__main__":
    unittest.main()
