"""`tools/run_tests_per_file.py`: the split of the test files into CI shards (pure logic, no Qt, no test is run).

CI starts one job per shard, each computing the split itself, so the shards together must be every file exactly once or a test
silently never runs.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import unittest

from tests._paths import REPO_ROOT

_SPEC = importlib.util.spec_from_file_location("run_tests_per_file", REPO_ROOT / "tools" / "run_tests_per_file.py")
runner = importlib.util.module_from_spec(_SPEC)
sys.modules["run_tests_per_file"] = runner  # a dataclass looks its own module up by name
_SPEC.loader.exec_module(runner)


def _listed(*extra: str) -> list[str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runner.main([*extra, "--list"])
    assert code == 0
    return buffer.getvalue().split()


class ParseShardTests(unittest.TestCase):
    def test_k_of_n(self) -> None:
        self.assertEqual(runner.parse_shard("2/3"), (2, 3))
        self.assertEqual(runner.parse_shard(" 1/1 "), (1, 1))

    def test_nonsense_is_refused(self) -> None:
        for text in ("0/3", "4/3", "1/0", "2", "a/b", "1/2/3", "-1/3", ""):
            with self.subTest(text=text), self.assertRaises(argparse.ArgumentTypeError):
                runner.parse_shard(text)


class PickShardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.files = runner.find_test_files("test_*.py")

    def test_the_shards_together_are_every_file_exactly_once(self) -> None:
        for count in (1, 2, 3, 4, 7):
            with self.subTest(shards=count):
                picked = [path for index in range(1, count + 1) for path in runner.pick_shard(self.files, index, count)]
                self.assertEqual(len(picked), len(set(picked)), "a file is in two shards")
                self.assertEqual(set(picked), set(self.files), "a file is in no shard")

    def test_one_shard_is_everything_in_the_same_order(self) -> None:
        self.assertEqual(runner.pick_shard(self.files, 1, 1), self.files)

    def test_the_same_call_gives_the_same_answer(self) -> None:
        self.assertEqual(runner.pick_shard(self.files, 2, 3), runner.pick_shard(self.files, 2, 3))

    def test_no_shard_is_much_bigger_than_the_rest(self) -> None:
        for count in (2, 3, 4):
            with self.subTest(shards=count):
                sizes = [sum(path.stat().st_size for path in runner.pick_shard(self.files, index, count)) for index in range(1, count + 1)]
                self.assertLess(max(sizes), 1.25 * sum(sizes) / count)

    def test_a_shard_keeps_the_biggest_first_order(self) -> None:
        shard = runner.pick_shard(self.files, 2, 3)
        sizes = [path.stat().st_size for path in shard]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class ListWithShardTests(unittest.TestCase):
    def test_the_listed_shards_add_up_to_the_listed_whole(self) -> None:
        whole = _listed()
        shards = [_listed("--shard", f"{index}/3") for index in (1, 2, 3)]
        self.assertEqual(sorted(name for shard in shards for name in shard), sorted(whole))
        self.assertTrue(all(shard for shard in shards), "an empty shard")

    def test_a_pattern_and_a_shard_combine(self) -> None:
        self.assertEqual(sorted(_listed("--pattern", "test_run_tests_per_file.py", "--shard", "1/1")), ["tests/unit/test_run_tests_per_file.py"])


if __name__ == "__main__":
    unittest.main()
