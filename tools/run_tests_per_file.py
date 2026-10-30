"""Run the test suite one process per test file, several files at a time (what CI runs; also the fast way to run everything here).

Why: one process for all 295 files gets slower with every test. A test's window is only scheduled for deletion, never destroyed, so each
`build_main_window()` has to cope with all the windows built before it (measured 2026-10-30: 3 s for the first window, 57 s for the
sixth), and the CI run grew to 3 to 5 hours. A fresh process per file starts clean, so the whole suite takes minutes.

What it cannot see: a failure that needs two files to run in the same process (one file leaves a font or the app theme behind and a
later file measures text). That is intended. Such a test should set up what it needs itself.

Uses `python -m unittest` (no pytest needed, CI installs none). A file that runs no test counts as a failure, so a file that was
silently skipped cannot pass.

A file that fails or times out while others run is run once more on its own at the end (`--no-retry` switches that off). If it
passes then, the run is green but the file is listed under "passed alone" with what went wrong the first time: a test that waits a
fixed time for a child process or a worker thread fails when the machine is busy, and that weakness should stay visible. If it
fails alone too, it is a real failure and the run is red.

Pinning every parallel file to its own core was tried on 2026-10-30 and removed: with 6 files at a time on 8 cores it was slower (14.2 min
against 12.7 min) and made two load-sensitive files fail (the warm pool test needs two workers). The files share all cores.

    python tools/run_tests_per_file.py                         # everything
    python tools/run_tests_per_file.py --pattern "test_*lspri*.py"
    python tools/run_tests_per_file.py --jobs 4 --timeout 600
    python tools/run_tests_per_file.py --list
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:  # a dependency of lspr-core and the LSPRi app; without it a timed-out file's children are not killed
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
DEFAULT_TIMEOUT_S = 600  # the slowest file takes about 3.5 min (2026-10-30); a hung one (the live worker test, under load) should not stall a run for longer
MAX_FAILURE_LINES = 150  # per failing file: enough for the tracebacks, never the whole log
MAX_FIRST_FAILURE_LINES = 40  # for a file that passed on retry: just enough to see what went wrong the first time

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_SKIPPED = re.compile(r"skipped=(\d+)")


@dataclass
class FileResult:
    path: Path
    status: str  # "ok" | "FAIL" | "NO TESTS" | "TIMEOUT"
    seconds: float
    tests: int
    skipped: int
    output: str
    first_attempt: FileResult | None = None  # set when this result is the retry of a file that did not pass the first time

    @property
    def passed(self) -> bool:
        return self.status == "ok"

    @property
    def name(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()


def find_test_files(pattern: str) -> list[Path]:
    """Every matching file under tests/, biggest first. A big file is usually a slow one, and starting the slow ones first keeps
    the last minutes from being a single long file on its own (longest-job-first scheduling)."""
    files = [path for path in TESTS_DIR.rglob(pattern) if path.is_file()]
    return sorted(files, key=lambda path: (-path.stat().st_size, path.as_posix()))


def parse_shard(text: str) -> tuple[int, int]:
    """'2/3' -> (2, 3): shard 2 of 3 (1-based)."""
    match = re.fullmatch(r"(\d+)/(\d+)", text.strip())
    if match is None or not 1 <= int(match.group(1)) <= int(match.group(2)):
        raise argparse.ArgumentTypeError(f"{text!r} is not K/N with 1 <= K <= N (for example 2/3)")
    return int(match.group(1)), int(match.group(2))


def pick_shard(files: list[Path], index: int, count: int) -> list[Path]:
    """The files that shard *index* of *count* runs (CI starts one job per shard, each on its own machine).

    Every job makes the same call on the same file list, so every file lands in exactly one shard. *files* must come in
    `find_test_files` order (biggest first): each file goes to the shard with the least file size so far. File size is only a stand-in
    for run time, but it divided the real times of 2026-10-30 well enough (3 shards: the worst one 15 % above an ideal split)."""
    loads = [0] * count
    mine = []
    for path in files:
        target = loads.index(min(loads))
        loads[target] += path.stat().st_size
        if target == index - 1:
            mine.append(path)
    return mine


def kill_tree(process: subprocess.Popen) -> None:
    """End a test process and everything it started (pool workers keep the output pipe open, which would block the reader)."""
    if psutil is not None:
        try:
            for child in psutil.Process(process.pid).children(recursive=True):
                child.kill()
        except psutil.Error:
            pass
    process.kill()


def run_one(path: Path, timeout_s: float) -> FileResult:
    """Run one file in a process of its own and report how it ended."""
    command = [sys.executable, "-X", "faulthandler", "-m", "unittest", path.relative_to(REPO_ROOT).as_posix()]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    start = time.perf_counter()
    process = subprocess.Popen(
        command, cwd=REPO_ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace"
    )
    try:
        output, _ = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            partial, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            partial = ""
        return FileResult(path, "TIMEOUT", time.perf_counter() - start, 0, 0, f"no result after {timeout_s:.0f} s\n{as_text(partial)}")
    seconds = time.perf_counter() - start
    ran = _RAN.search(output)
    tests = int(ran.group(1)) if ran else 0
    skipped_match = _SKIPPED.search(output.rsplit("Ran ", 1)[-1]) if ran else None
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    if process.returncode == 0:
        status = "ok"
    elif process.returncode == 5 or (ran and tests == 0):
        status = "NO TESTS"
    else:
        status = "FAIL"
    return FileResult(path, status, seconds, tests, skipped, output)


def retry_alone(results: list[FileResult], timeout_s: float) -> list[FileResult]:
    """Run each file that failed or timed out once more, on its own, after all the parallel runs are over.

    Several files at a time load the machine, and a few tests wait a fixed time for a child process or a worker thread (the warm
    pool test waits for its workers, the live worker test for a child process; the live worker test gave a child 5 s until 2026-10-30). Measured 2026-10-30: four such
    files failed with six running at once and passed when run alone. A file that passes the second time is NOT reported as clean: it
    is listed with its first failure at the end, so the weakness stays visible. A file that fails alone stays failed. A NO TESTS
    file is not retried (running it again cannot change that)."""
    again = [result for result in results if result.status in ("FAIL", "TIMEOUT")]
    if not again:
        return results
    print(f"\nRunning {len(again)} file(s) that did not pass once more, one at a time, with nothing else running:", flush=True)
    by_name = {result.name: result for result in results}
    for first in sorted(again, key=lambda result: result.name):
        second = run_one(first.path, timeout_s)
        second.first_attempt = first
        by_name[first.name] = second
        print(f"  {second.status:8s} {second.seconds:7.1f} s  {second.name}  ({second.tests} tests)", flush=True)
    return [by_name[result.name] for result in results]


def as_text(value: str | bytes | None) -> str:
    """`TimeoutExpired` hands back the partial output as bytes even when the run asked for text."""
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def tail(text: str, lines: int) -> str:
    kept = text.splitlines()
    if len(kept) <= lines:
        return text.rstrip()
    return f"... ({len(kept) - lines} earlier lines left out)\n" + "\n".join(kept[-lines:])


def main(argv: list[str] | None = None) -> int:
    cpus = os.cpu_count() or 1
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pattern", default="test_*.py", help="file name pattern under tests/ (default: %(default)s)")
    parser.add_argument("--jobs", type=int, default=max(1, cpus - 1), help="files at a time (default: every core but one = %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds one file may take (default: %(default)s)")
    parser.add_argument("--shard", type=parse_shard, default=None, metavar="K/N", help="run only shard K of N (CI: one job per shard); the shards together are every file once")
    parser.add_argument("--no-retry", action="store_true", help="do not run a failed file again on its own (see the end of this file's docstring)")
    parser.add_argument("--list", action="store_true", help="print the files that would run and stop")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # a stream captured by a test has none
            stream.reconfigure(encoding="utf-8", errors="replace")  # test output may hold characters the Windows console cannot show

    files = find_test_files(args.pattern)
    shard_note = ""
    if args.shard is not None:
        files = pick_shard(files, *args.shard)
        shard_note = f"shard {args.shard[0]} of {args.shard[1]}: "
    if args.list:
        print("\n".join(path.relative_to(REPO_ROOT).as_posix() for path in files))
        return 0
    if not files:
        print(f"No test file matches {args.pattern!r} under {TESTS_DIR}.", file=sys.stderr)
        return 1

    print(f"{shard_note}{len(files)} test files, {args.jobs} at a time ({cpus} cores), {args.timeout:.0f} s limit per file, Python {sys.version.split()[0]}", flush=True)
    started = time.perf_counter()
    results: list[FileResult] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, path, args.timeout) for path in files]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            extra = f", {result.skipped} skipped" if result.skipped else ""
            print(f"[{len(results):3d}/{len(files)}] {result.status:8s} {result.seconds:7.1f} s  {result.name}  ({result.tests} tests{extra})", flush=True)

    if not args.no_retry:
        results = retry_alone(results, args.timeout)

    wall = time.perf_counter() - started
    failed = sorted((result for result in results if not result.passed), key=lambda result: result.name)
    print()
    print(f"{sum(r.tests for r in results)} tests in {len(results)} files, {sum(r.skipped for r in results)} skipped, "
          f"{wall / 60:.1f} min wall time ({sum(r.seconds for r in results) / 60:.1f} min of work)")
    print("slowest files:")
    for result in sorted(results, key=lambda r: -r.seconds)[:8]:
        print(f"  {result.seconds:7.1f} s  {result.name}")

    flaky = sorted((result for result in results if result.first_attempt is not None and result.passed), key=lambda result: result.name)
    if flaky:
        print(f"\n{len(flaky)} FILE(S) FAILED WITH OTHER FILES RUNNING AND PASSED ALONE (load-sensitive: a test that waits a fixed time for a "
              "child process or a worker thread; worth making robust, not a red run)")
        for result in flaky:
            first = result.first_attempt
            print(f"\n----- {result.name}: first run {first.status} after {first.seconds:.1f} s, retry ok after {result.seconds:.1f} s -----")
            print(tail(first.output, MAX_FIRST_FAILURE_LINES))

    if failed:
        print(f"\n{len(failed)} FILE(S) DID NOT PASS")
        for result in failed:
            print(f"\n===== {result.status}: {result.name} ({result.seconds:.1f} s) =====")
            print(tail(result.output, MAX_FAILURE_LINES))
        print("\nDid not pass: " + ", ".join(result.name for result in failed))
        return 1
    print("\nAll files passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
