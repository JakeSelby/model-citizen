"""`summarise` reads stratum folders only when every row in them names its stratum (#1182): any other
nested set leaves the results path missing whatever the flags, and `--pool` over one stratum is
refused. No test builds an image or calls a model."""
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH
from test_replay_strata import stratum_rows, summarise_args, write_rows


def unstratified(model):
    """A single-model run's rows, which carry no stratum."""
    rows = stratum_rows(model)
    for row in rows:
        del row["stratum"]
    return rows


class MissingResultsTests(unittest.TestCase):
    def assert_missing(self, folder, pool):
        with self.assertRaises(SystemExit) as caught:
            BENCH.cmd_summarise(summarise_args(folder, pool=pool))
        self.assertIn("does not exist", str(caught.exception))

    def test_per_tag_folders_without_strata_leave_the_path_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for tag in ("v1", "v2"):
                write_rows(Path(tmp) / tag / BENCH.RESULTS, unstratified("claude-a"))
            for pool in (False, True):
                self.assert_missing(tmp, pool)

    def test_one_folder_without_a_stratum_spoils_the_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_rows(Path(tmp) / "claude-a" / BENCH.RESULTS, stratum_rows("claude-a"))
            write_rows(Path(tmp) / "v1" / BENCH.RESULTS, unstratified("claude-b"))
            for pool in (False, True):
                self.assert_missing(tmp, pool)

    def test_an_empty_folder_is_missing_with_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            for pool in (False, True):
                self.assert_missing(tmp, pool)
            self.assert_missing(str(Path(tmp) / "absent" / BENCH.RESULTS), True)


class PoolOfOneTests(unittest.TestCase):
    def assert_refused(self, results):
        with self.assertRaises(SystemExit) as caught:
            BENCH.cmd_summarise(summarise_args(results, pool=True))
        self.assertIn("--pool needs rows from two or more strata", str(caught.exception))
        self.assertIn("nothing to pool", str(caught.exception))

    def test_pool_on_a_single_model_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, unstratified("claude-a"))
            self.assert_refused(results)

    def test_pool_on_a_tag_folder_of_one_stratum_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_rows(Path(tmp) / "claude-a" / BENCH.RESULTS, stratum_rows("claude-a"))
            self.assert_refused(tmp)


if __name__ == "__main__":
    unittest.main()
