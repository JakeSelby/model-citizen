# SPDX-License-Identifier: MIT
"""The Studio's performance budgets (AH-S316): the judging logic, and the live measurements.

The budgets and how each is judged are in ``scripts/studio_budgets.py``. The live tests here
serve a fixture checkout to a fixture home (``studio_e2e_support``) and measure the first load
in headless Chrome and the library route's p95 with 500 fixture modules beside the core ones.
They run only with ``STUDIO_E2E=1``; the judging tests run everywhere.
"""
from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import studio_e2e_support as support

REPO = support.REPO
spec = importlib.util.spec_from_file_location("studio_budgets", REPO / "scripts" / "studio_budgets.py")
budgets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budgets)

FIXTURE_MODULES = 500
LOADS = 5
WARM_UP = 5
SAMPLES = 120


class BudgetJudgingTests(unittest.TestCase):
    def test_p95_is_the_nearest_rank_and_median_the_middle(self):
        samples = list(range(1, 101))
        self.assertEqual(budgets.p95(samples), 95)
        self.assertEqual(budgets.p95([7.0]), 7.0)
        self.assertEqual(budgets.median([3, 1, 2]), 2)
        self.assertEqual(budgets.median([4, 1, 3, 2]), 2.5)
        with self.assertRaises(ValueError):
            budgets.p95([])

    def test_a_figure_over_its_budget_fails_naming_the_budget(self):
        within = {"first_load.slowest_ms": 1500.0, "api.library_p95_ms": 99.0}
        self.assertEqual(budgets.over_budget(within), [])
        failures = budgets.over_budget({"first_load.slowest_ms": 1500.1,
                                        "api.library_p95_ms": 100.5})
        self.assertEqual(len(failures), 2)
        self.assertIn("budget api.library_p95_ms: measured 100.5 ms", failures[0])
        self.assertIn("budget first_load.slowest_ms", failures[1])
        with self.assertRaises(KeyError):
            budgets.over_budget({"no.such_budget_ms": 1.0})

    def test_the_committed_bundle_fits_its_budgets(self):
        measured = budgets.bundle_sizes(REPO / "studio" / "dist")
        self.assertEqual(set(measured), {name for name in budgets.BUDGETS
                                         if name.startswith("bundle.")})
        self.assertEqual(budgets.over_budget(measured), [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(budgets.main(["bundle", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["failures"], [])

    def test_a_bundle_over_its_size_budget_fails_and_names_the_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary) / "dist"
            shutil.copytree(REPO / "studio" / "dist", dist)
            script = next((dist / "assets").glob("*.js"))
            # Incompressible bytes, so gzip cannot fold the growth away.
            with script.open("ab") as handle:
                handle.write(os.urandom(400 * 1024))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = budgets.main(["bundle", "--dist", str(dist)])
        self.assertEqual(code, 1)
        self.assertIn("budget bundle.script_gzip_bytes", output.getvalue())

    def test_a_dist_without_a_script_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            (dist / "index.html").write_text("<html></html>", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(budgets.main(["bundle", "--dist", str(dist)]), 2)


class LiveBudgetTests(support.StudioE2E):
    """First load and API latency against a served fixture Studio, judged by the budgets."""

    def prepare_home(self):
        root = support.write_library(self.home / "fixture-library", FIXTURE_MODULES)
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["primitive_roots"] = [str(root)]
        self.config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def test_first_load_renders_the_shell_within_budget(self):
        """Navigation start to the rendered shell, cache disabled; every one of five loads must
        fit the budget, so the slowest is judged."""
        self.open()
        self.devtools.call("Network.setCacheDisabled", {"cacheDisabled": True})
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__shellAt = null;
            new MutationObserver((_, observer) => {
              if (document.querySelector('.studio-frame')) {
                globalThis.__shellAt = performance.now();
                observer.disconnect();
              }
            }).observe(document, { childList: true, subtree: true });
        """})
        loads = []
        for _ in range(LOADS):
            # Each document has its own time origin; reading __shellAt only once the origin has
            # changed keeps a poll from reading the previous document's figure.
            before = self.js("performance.timeOrigin")
            # A reload, not a navigation: a same-URL navigation with a fragment loads nothing.
            self.devtools.call("Page.reload", {"ignoreCache": True})
            loads.append(self.wait("performance.timeOrigin !== %r && globalThis.__shellAt"
                                   % before, "the shell did not render", seconds=30))
        measured = {"first_load.slowest_ms": max(loads)}
        print("\nfirst load (ms): %s; slowest %.1f" % (
            ", ".join("%.0f" % value for value in loads), measured["first_load.slowest_ms"]))
        self.assertEqual(budgets.over_budget(measured), [], loads)
        self.assert_no_network()
        self.assert_no_model_calls()

    def _served_ms(self, path: str) -> float:
        """The route's time from request to the last body byte, on an open connection: the
        TCP connect and the client's JSON parsing are outside the timer."""
        connection = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=30)
        try:
            connection.connect()
            started = time.perf_counter()
            connection.request("GET", path, headers={"Host": self.record["host"],
                                                     "Cookie": self.cookie})
            response = connection.getresponse()
            body = response.read()
            elapsed = (time.perf_counter() - started) * 1000
        finally:
            connection.close()
        self.assertEqual(response.status, 200, body[:200])
        return elapsed

    def test_library_api_p95_over_five_hundred_modules_is_within_budget(self):
        status, library = self.api("GET", "/api/library")
        self.assertEqual(status, 200, library)
        modules = library["summary"]["modules"]
        self.assertEqual(modules, len(library["modules"]))
        self.assertGreaterEqual(modules, FIXTURE_MODULES)
        for _ in range(WARM_UP):
            self.api("GET", "/api/library")
        samples = [self._served_ms("/api/library") for _ in range(SAMPLES)]
        measured = {"api.library_p95_ms": budgets.p95(samples)}
        print("\nlibrary p95 %.1f ms, median %.1f ms over %d samples and %d modules" % (
            measured["api.library_p95_ms"], budgets.median(samples), len(samples), modules))
        self.assertEqual(budgets.over_budget(measured), [])
        self.assert_no_model_calls()


if __name__ == "__main__":
    unittest.main()
