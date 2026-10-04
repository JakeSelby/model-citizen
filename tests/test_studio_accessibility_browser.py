# SPDX-License-Identifier: MIT
"""WCAG 2.2 AA checks on every rendered Studio route, at phone and desktop width, in both themes.

The rule set is ``studio_a11y_checks.js``: hand-written, because axe-core is MPL-2.0 and the
repository takes no copyleft dependency. The keyboard test drives real ``Tab`` presses through
DevTools, so ``:focus-visible`` styles apply as they do for a keyboard user.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

import draft_support
import test_studio_browser as browser_support

CLI = browser_support.CLI
CHECKS = (Path(__file__).resolve().parent / "studio_a11y_checks.js").read_text(encoding="utf-8")

# Every routed screen. The unknown run id renders the run page's not-found state.
ROUTES = (
    "/", "/setup", "/configure", "/library", "/experiments", "/experiments/runs/no-such-run",
    "/activity", "/reports", "/reports/rules", "/reports/usage", "/reports/trends",
)
FIXTURES = browser_support.REPO / "studio" / "tests" / "fixtures"
# Report routes in their populated state, served from the committed fixtures the frontend unit
# tests read, so charts, tables and legends are audited and not only the empty states.
POPULATED = ("/reports/trends", "/reports/rules", "/reports/usage")
POPULATE = """
(() => {
  const fixtures = { trends: %s, rules: %s, spend: %s };
  const original = globalThis.fetch.bind(globalThis);
  const reply = (body) => Promise.resolve(new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' } }));
  globalThis.fetch = (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (url === '/api/reports/trends') return reply(fixtures.trends);
    if (url === '/api/rules/health') return reply(fixtures.rules);
    if (url === '/api/reports/spend') {
      const by = JSON.parse(init.body || '{}').by;
      return reply(fixtures.spend[by] || fixtures.spend.session);
    }
    return original(input, init);
  };
})();
"""
PHONE = 320
DESKTOP = 1280

# Settled: no loader or busy region, a level-one heading, and the text unchanged across polls.
SETTLED = ("(() => { const busy = document.querySelector('[aria-busy=true], .mantine-Loader-root');"
           " const text = document.body.innerText; const stable = text === globalThis.__a11yText;"
           " globalThis.__a11yText = text;"
           " return !busy && document.querySelector('main h1') !== null && stable; })()")

# Where focus is after a Tab press, and whether a keyboard user can see it.
FOCUS_STATE = r"""
(() => {
  const element = document.activeElement;
  if (!element || element === document.body || element === document.documentElement) {
    return JSON.stringify({ node: null });
  }
  const parse = (value) => (value.match(/[\d.]+/g) || []).map(Number);
  const luminance = (rgb) => {
    const linear = rgb.slice(0, 3).map((value) => {
      const scaled = value / 255;
      return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };
  const ratio = (a, b) => {
    const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (light + 0.05) / (dark + 0.05);
  };
  const backdrop = (node) => {
    for (; node && node.nodeType === 1; node = node.parentElement) {
      const color = parse(getComputedStyle(node).backgroundColor);
      if (color.length >= 3 && (color.length === 3 || color[3] === 1)) return color;
    }
    return parse(getComputedStyle(document.body).backgroundColor);
  };
  const style = getComputedStyle(element);
  const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
  const outlineContrast = outline ? ratio(parse(style.outlineColor), backdrop(element.parentElement)) : 0;
  const ring = style.boxShadow && style.boxShadow !== 'none';
  const rect = element.getBoundingClientRect();
  const points = [[rect.left + rect.width / 2, rect.top + rect.height / 2],
    [rect.left + 2, rect.top + 2], [rect.right - 2, rect.top + 2],
    [rect.left + 2, rect.bottom - 2], [rect.right - 2, rect.bottom - 2]];
  const covered = points.every(([x, y]) => {
    const hit = document.elementFromPoint(x, y);
    return hit && !element.contains(hit) && !hit.contains(element);
  });
  const offscreen = rect.bottom < 0 || rect.top > innerHeight || rect.width === 0;
  globalThis.__a11yVisits = globalThis.__a11yVisits || 0;
  const text = (element.getAttribute('aria-label') || element.innerText || element.value || '')
    .trim().replace(/\s+/g, ' ').slice(0, 50);
  return JSON.stringify({
    node: element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') + ' "' + text + '"',
    revisit: element.dataset.a11yVisit || null,
    visit: element.dataset.a11yVisit || (element.dataset.a11yVisit = String(++globalThis.__a11yVisits)),
    visible: (outline && outlineContrast >= 3) || Boolean(ring),
    outline: style.outlineStyle + ' ' + style.outlineWidth + ' ' + style.outlineColor + ' ' + outlineContrast.toFixed(2),
    obscured: covered && !offscreen,
  });
})()
"""


class StudioAccessibilityBrowserTests(unittest.TestCase):
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio

    def setUp(self):
        browser_support.StudioBrowserTests.setUp(self)
        self.env["HARNESS_WORKTREE_ROOT"] = str(Path(self.temporary.name) / "worktrees")
        config_path = self.home / ".config" / "agent-harness" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes((browser_support.REPO / "config.example.json").read_bytes())

    # Driving.
    def _open(self, width: int, scheme: str) -> None:
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self._viewport(width, scheme)
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()

    def _viewport(self, width: int, scheme: str) -> None:
        self.devtools.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": scheme},
            {"name": "prefers-reduced-motion", "value": "reduce"},
        ]})
        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": 800, "deviceScaleFactor": 1, "mobile": width < 600,
        })

    def _wait(self, expression: str, message: str, seconds: float = 20.0) -> Any:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                value = self.devtools.evaluate(expression)
            except RuntimeError:
                value = None
            if value:
                return value
            time.sleep(0.1)
        self.fail(message)

    def _populate(self) -> None:
        def fixture(name: str) -> str:
            return (FIXTURES / name).read_text(encoding="utf-8")
        self.devtools.call("Page.enable")
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": POPULATE % (
            fixture("trends.json"), fixture("rule-health.json"), fixture("spend.json"))})

    def _visit(self, route: str, scheme: str) -> None:
        self.devtools.evaluate("location.hash = %s" % json.dumps("#" + route))
        self._wait("document.documentElement.dataset.mantineColorScheme === %s" % json.dumps(scheme),
                   "the %s theme did not apply" % scheme)
        self._wait(SETTLED, "%s did not settle" % route, seconds=30.0)

    def _audit(self) -> Dict[str, Any]:
        return json.loads(self.devtools.evaluate(CHECKS))

    def _press_tab(self) -> None:
        for kind in ("rawKeyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9,
            })

    def _report(self, failures: List[str]) -> None:
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    # Tests.
    def test_every_route_meets_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        failures: List[str] = []
        audits = 0
        for width in (PHONE, DESKTOP):
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                for route in ROUTES:
                    self._visit(route, scheme)
                    result = self._audit()
                    audits += 1
                    self.assertEqual(result["width"], width)
                    self.assertGreater(result["checked"]["text"], 0, route)
                    self.assertGreater(result["checked"]["controls"], 0, route)
                    for violation in result["violations"]:
                        failures.append("%s @%dpx %s [%s] %s: %s" % (
                            route, width, scheme, violation["rule"], violation["node"],
                            violation["detail"]))
        self.assertEqual(audits, len(ROUTES) * 4)
        self._report(failures)

    def test_populated_reports_meet_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        self._populate()
        failures: List[str] = []
        for width in (PHONE, DESKTOP):
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                for route in POPULATED:
                    self._visit(route, scheme)
                    self.assertFalse(self.devtools.evaluate("document.querySelector('[role=alert]') !== null"),
                                     "%s did not render its fixture" % route)
                    result = self._audit()
                    self.assertGreater(result["checked"]["text"], 0, route)
                    for violation in result["violations"]:
                        failures.append("%s populated @%dpx %s [%s] %s: %s" % (
                            route, width, scheme, violation["rule"], violation["node"],
                            violation["detail"]))
        self._report(failures)

    def test_every_rule_fires_on_a_planted_violation(self):
        # Proves the rule set bites: each planted defect must be reported by its own rule.
        self._open(PHONE, "light")
        self._visit("/", "light")
        self.devtools.evaluate(r"""(() => {
          const main = document.querySelector('main');
          const planted = document.createElement('div');
          planted.id = 'planted';
          planted.innerHTML = `
            <h4>Skipped heading level</h4>
            <p style="color:#bbbbbb;background:#ffffff">Faint text</p>
            <button aria-label="Launch">Start run</button>
            <button></button>
            <a href="#/" style="display:inline-block;width:10px;height:10px;overflow:hidden">x</a><a href="#/" style="display:inline-block;width:10px;height:10px;overflow:hidden">y</a>
            <input aria-label="Faint field" style="border:1px solid #f4f4f4;background:#ffffff">
            <span aria-labelledby="missing-id">Dangling reference</span>
            <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
            <div style="overflow:auto;width:100px;height:40px"><div style="width:400px;height:80px">Unreachable scroller</div></div>
            <div style="width:900px">Too wide</div>`;
          main.appendChild(planted);
          const stray = document.createElement('p');
          stray.id = 'planted-stray';
          stray.textContent = 'Outside every landmark';
          document.querySelector('.studio-frame').appendChild(stray);
        })()""")
        try:
            rules = {violation["rule"] for violation in self._audit()["violations"]}
        finally:
            self.devtools.evaluate("document.getElementById('planted').remove();"
                                   " document.getElementById('planted-stray').remove()")
        expected = {"heading-order", "color-contrast", "label-in-name", "control-name", "target-size",
                    "non-text-contrast", "aria-valid-reference", "image-alt",
                    "scrollable-region-focusable", "reflow", "region"}
        self.assertEqual(expected - rules, set())
        self.assertEqual(self._audit()["violations"], [])

    def test_a_loaded_draft_meets_the_aa_rules_at_phone_and_desktop_width(self):
        draft = draft_support.draft_name("a11y-browser-")
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", draft, "--json"],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        draft_support.register_draft_cleanup(self, draft, self.env, stop=self._stop_studio)
        failures: List[str] = []
        for width in (PHONE, DESKTOP):
            self._open(width, "light")
            self._visit("/configure", "light")
            self.devtools.evaluate(
                "(() => { const label = [...document.querySelectorAll('label')]"
                ".find(item => item.textContent.trim().startsWith('Draft name'));"
                " const input = document.getElementById(label.htmlFor);"
                " Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, %s);"
                " input.dispatchEvent(new Event('input', { bubbles: true })); })()" % json.dumps(draft))
            self._wait("[...document.querySelectorAll('button')].some(b => b.textContent === 'Load draft' && !b.disabled)",
                       "draft load did not enable")
            self.devtools.evaluate(
                "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()")
            self._wait("document.body.textContent.includes('Draft configuration is ready.')",
                       "draft configuration did not load", seconds=60.0)
            self._wait(SETTLED, "the loaded draft did not settle", seconds=30.0)
            for violation in self._audit()["violations"]:
                failures.append("draft @%dpx [%s] %s: %s" % (
                    width, violation["rule"], violation["node"], violation["detail"]))
        self._report(failures)

    def test_tab_reaches_every_route_control_with_visible_focus_and_no_trap(self):
        failures: List[str] = []
        self._open(PHONE, "light")
        for route in ROUTES:
            self._visit(route, "light")
            # One stop per radio group: Tab enters a group once and the arrows move within it.
            focusable = self.devtools.evaluate(
                "(() => { const groups = new Set(); return [...document.querySelectorAll('a[href], button:not([disabled]),"
                " input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary,"
                " [tabindex]')].filter(node => node.tabIndex >= 0 && node.checkVisibility({ visibilityProperty: true })"
                " && !node.closest('[inert], [aria-hidden=true], fieldset[disabled]'))"
                ".filter(node => node.type !== 'radio' || !groups.has(node.name) && groups.add(node.name)).length; })()")
            self.devtools.evaluate(
                "document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0);"
                " document.querySelector('.skip-link').focus();"
                " globalThis.__a11yVisits = 0;"
                " document.querySelectorAll('[data-a11y-visit]').forEach(node => delete node.dataset.a11yVisit)")
            seen: List[str] = []
            left_page = False
            previous, stays = None, 0
            # The pass starts on the skip link, the first stop of every page, and Tab walks on.
            for press in range(3 * focusable + 10):
                if press:
                    self._press_tab()
                # Let a focus transition finish, so the ring is read as the user sees it.
                time.sleep(0.05)
                state = json.loads(self.devtools.evaluate(FOCUS_STATE))
                if state["node"] is None:
                    left_page = True
                    break
                if state["revisit"] and state["revisit"] == previous:
                    # A date field's day, month and year are separate stops on one element.
                    stays += 1
                    if stays > 3:
                        failures.append("%s [keyboard-trap] Tab stays on %s" % (route, state["node"]))
                        left_page = True
                        break
                    continue
                stays = 0
                previous = state["visit"]
                if state["revisit"]:
                    # Wrapping to the first stop ends the pass; returning anywhere else is a cycle.
                    if state["revisit"] != "1":
                        failures.append("%s [keyboard-trap] focus cycled back to %s" % (route, state["node"]))
                    left_page = True
                    break
                seen.append(state["node"])
                if not state["visible"]:
                    failures.append("%s [focus-visible] %s: %s" % (route, state["node"], state["outline"]))
                if state["obscured"]:
                    failures.append("%s [focus-not-obscured] %s" % (route, state["node"]))
            if not left_page:
                failures.append("%s [keyboard-trap] Tab never left the page after %d presses" % (route, len(seen)))
            if len(seen) < focusable:
                failures.append("%s [keyboard] Tab reached %d of %d focusable controls: %s" % (
                    route, len(seen), focusable, " > ".join(seen)))
            first = self.devtools.evaluate("document.querySelector('.skip-link') !== null")
            self.assertTrue(first, route)
        self._report(failures)


if __name__ == "__main__":
    unittest.main()
