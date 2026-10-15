# SPDX-License-Identifier: MIT
"""The operational look as rendered: shell geometry, compact Home rows and no boxed sections."""
from __future__ import annotations

import datetime
import json
import unittest

import test_studio_accessibility_browser as a11y

# Every page restyled from cards and papers to hairline sections.
PAGES = ("/changes", "/harness/sources", "/evals", "/evals/runs/no-such-run", "/harness/hooks",
         "/evals/trends", "/harness/rules", "/sessions", "/baseline")
SHELL = """JSON.stringify((() => {
  const navbar = document.querySelector('.studio-navbar');
  const header = document.querySelector('.studio-header');
  const main = document.querySelector('main');
  const nav = getComputedStyle(navbar);
  return { navbar: navbar.getBoundingClientRect().width, borderWidth: nav.borderRightWidth,
    borderStyle: nav.borderRightStyle, borderColor: nav.borderRightColor,
    header: header.getBoundingClientRect().height, headerBottom: header.getBoundingClientRect().bottom,
    mainTop: main.getBoundingClientRect().top, headerOverflow: header.scrollHeight - header.clientHeight,
    toolsOverflow: [...header.children].some(child => child.getBoundingClientRect().bottom > header.getBoundingClientRect().bottom + 0.5)
      || [...header.querySelectorAll('*')].some(node => node.getBoundingClientRect().right > header.getBoundingClientRect().right + 0.5) };
})())"""
HOME_ROWS = """JSON.stringify((() => {
  const rows = [...document.querySelectorAll('tr[data-item-id]')];
  const figures = [...document.querySelectorAll('.home-figure')];
  const mono = [...document.querySelectorAll('main *')].filter(node => node.childNodes.length
      && [...node.childNodes].some(child => child.nodeType === 3 && child.textContent.trim())
      && getComputedStyle(node).fontFamily.startsWith('"JetBrains Mono"'))
    .filter(node => !node.closest('code, pre, .run-id, .code-view, .page-command'))
    .map(node => node.tagName + '.' + node.className);
  return { rows: rows.map(row => row.getBoundingClientRect().height),
    sizes: [...new Set(rows.map(row => getComputedStyle(row.querySelector('td:nth-child(2) p') || row).fontSize))],
    figureTops: [...new Set(figures.map(figure => Math.round(figure.getBoundingClientRect().top)))],
    figures: figures.length, mono,
    ids: [...document.querySelectorAll('.page-command')].every(node => getComputedStyle(node).fontFamily.startsWith('"JetBrains Mono"')) };
})())"""
BOXED = ("JSON.stringify([...document.querySelectorAll('main [data-with-border], main .mantine-Card-root')]"
         ".map(node => node.className.toString()))")


class OperationalLookBrowserTests(unittest.TestCase):
    setUp = a11y.StudioAccessibilityBrowserTests.setUp
    _bootstrap = a11y.StudioAccessibilityBrowserTests._bootstrap
    _wait_for_shell = a11y.StudioAccessibilityBrowserTests._wait_for_shell
    _close_devtools = a11y.StudioAccessibilityBrowserTests._close_devtools
    _stop_browser = a11y.StudioAccessibilityBrowserTests._stop_browser
    _stop_studio = a11y.StudioAccessibilityBrowserTests._stop_studio
    _open = a11y.StudioAccessibilityBrowserTests._open
    _viewport = a11y.StudioAccessibilityBrowserTests._viewport
    _wait = a11y.StudioAccessibilityBrowserTests._wait
    _visit = a11y.StudioAccessibilityBrowserTests._visit
    _populate = a11y.StudioAccessibilityBrowserTests._populate
    _seed_transcripts = a11y.StudioAccessibilityBrowserTests._seed_transcripts
    _install_stubs = a11y.StudioAccessibilityBrowserTests._install_stubs

    def _go(self, route: str) -> None:
        # Geometry needs the page rendered, not its text frozen: a live doctor count may still move.
        self.devtools.evaluate("location.hash = %s" % json.dumps("#" + route))
        self._wait("location.hash === %s && document.querySelector('main h1') !== null"
                   " && !document.querySelector('[aria-busy=true], .mantine-Loader-root')"
                   " && !document.body.innerText.includes('Reading where setup stands')" % json.dumps("#" + route),
                   "%s did not render" % route, seconds=60.0)

    def test_the_shell_is_a_220_px_navbar_with_a_hairline_and_a_header_that_never_covers_the_page(self):
        for width in (1440, 1100):
            self._open(width, "light")
            self._go("/home")
            shell = json.loads(self.devtools.evaluate(SHELL))
            self.assertEqual(shell["navbar"], 220, width)
            self.assertEqual((shell["borderWidth"], shell["borderStyle"], shell["borderColor"]),
                             ("1px", "solid", "rgb(233, 236, 239)"), width)
            self.assertGreaterEqual(shell["header"], 52, width)
            # At 1100 px the tools wrap inside a taller header instead of painting over the page.
            self.assertLessEqual(shell["headerBottom"], shell["mainTop"] + 0.5, width)
            self.assertLessEqual(shell["headerOverflow"], 0, width)
            self.assertFalse(shell["toolsOverflow"], width)
        self._open(1440, "dark")
        self._go("/home")
        # The scheme attribute flips on the media query's change event, a frame after emulation.
        self._wait("document.documentElement.dataset.mantineColorScheme === 'dark'",
                   "the shell did not switch to the dark scheme")
        self.assertEqual(json.loads(self.devtools.evaluate(SHELL))["borderColor"], "rgb(44, 47, 52)")

    def test_home_rows_are_compact_and_monospace_only_for_ids_and_commands(self):
        from test_studio_surface_hooks import decision
        from harness_core.studio import periods

        # grade-bash fired ten days ago and not since, so Home's queue holds a silent-hook row.
        when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
        log = periods.state_dir(self.home) / "decisions.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(decision(when, "grade-bash", "allow")) + "\n", encoding="utf-8")
        self._open(1440, "light")
        self._go("/home")
        # The first Home read runs the rule-health engines of the fixture checkout.
        self._wait("document.querySelector('tr[data-item-id]') !== null", "Home's queue rows did not render",
                   seconds=180.0)
        home = json.loads(self.devtools.evaluate(HOME_ROWS))
        self.assertTrue(home["rows"])
        for height in home["rows"]:
            self.assertGreaterEqual(height, 26)
            self.assertLessEqual(height, 96)
        self.assertEqual(home["sizes"], ["13px"])
        # The digest is one hairline strip of figures, never a stack of cards.
        self.assertGreater(home["figures"], 1)
        self.assertEqual(len(home["figureTops"]), 1)
        self.assertEqual(home["mono"], [])
        self.assertTrue(home["ids"])

    def test_no_restyled_page_draws_a_bordered_card_or_paper(self):
        self._populate()
        self._open(1440, "light")
        self._install_stubs()
        for route in PAGES:
            self._go(route)
            self.assertEqual(json.loads(self.devtools.evaluate(BOXED)), [], route)
            self.assertGreater(self.devtools.evaluate("document.querySelectorAll('main *').length"), 10, route)


if __name__ == "__main__":
    unittest.main()
