# SPDX-License-Identifier: MIT
"""WCAG 2.2 AA checks on every rendered Studio route, at phone and desktop width, in both themes.

The rule set is ``studio_a11y_checks.js``: hand-written, because axe-core is MPL-2.0 and the
repository takes no copyleft dependency. The keyboard test drives real ``Tab`` presses through
DevTools, so ``:focus-visible`` styles apply as they do for a keyboard user.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

import draft_support
import studio_e2e_support as e2e_support
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
# Populated states the test home cannot produce, served through one fetch stub. Each answer is a
# committed fixture: the engine payloads the frontend unit tests read, and `a11y-states.json`, whose
# entries name the unit test or response type they come from. Every other POST passes only when it
# is a read on the allow-list; anything else is refused and recorded, so no click starts real work.
STUBS = r"""
(() => {
  const fixtures = Object.fromEntries(Object.entries(globalThis.__a11yFixtures)
    .map(([name, text]) => [name, JSON.parse(text)]));
  const states = Object.fromEntries(Object.entries(fixtures.states).filter(([key]) => !key.startsWith('_'))
    .map(([key, value]) => [key, Object.fromEntries(Object.entries(value).filter(([field]) => !field.startsWith('_')))]));
  const READS = new Set(['/api/activity', '/api/configure/authoring/library', '/api/configure/authoring/preview',
    '/api/configure/authoring/read', '/api/configure/module/preview', '/api/configure/module/read',
    '/api/configure/preview', '/api/configure/read', '/api/configure/schema', '/api/configure/selection/preview',
    '/api/configure/selection/read', '/api/configure/test/plan', '/api/evals/catalog', '/api/library',
    '/api/overview', '/api/reports/spend', '/api/reports/trends', '/api/rules/health', '/api/runs/case-history',
    '/api/runs/catalog', '/api/runs/detail', '/api/runs/history', '/api/runs/show', '/api/selection',
    '/api/first-run']);
  const original = globalThis.fetch.bind(globalThis);
  const reply = (body) => Promise.resolve(new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' } }));
  const targets = [states.replayTable.rows[0].target, states.replayTable.rows[2].target];
  globalThis.__a11yRefused = [];
  globalThis.fetch = (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    const method = (init.method || (typeof input === 'string' ? 'GET' : input.method) || 'GET').toUpperCase();
    const body = JSON.parse(init.body || '{}');
    switch (url) {
      case '/api/runs/compare': return reply(fixtures.compare);
      // The test home's replay catalog lists no task; one is enough to fill the launch form.
      case '/api/runs/replay/catalog': return original(input, init).then((answer) => answer.json())
        .then((catalog) => reply({ ...catalog, packs: [], default_pack: null,
          tasks: [{ id: 'fixture-task', label: 'fixture-task' }] }));
      case '/api/runs/replay/preview': return reply(states.replayPreview);
      case '/api/runs/replay/start': return reply({ run_id: 'fixture-replay' });
      case '/api/runs/replay/result': return reply({ schema_version: 1, progress: states.replayProgress.rows,
        run: { run_id: 'fixture-replay', status: 'succeeded' },
        result: { targets, table: states.replayTable.rows, spend_usd: 7.4, reported_spend_usd: 7.4, spend_cap_usd: '20',
          stopped_at_cap: false, analysis: fixtures.replay, analysis_error: null, comparisons: [] } });
      case '/api/evals/run': return reply({ run_id: 'fixture-hook-matrix', command: 'citizen evals run hook-matrix' });
      case '/api/evals/preview': return reply({ ...states.evalPreview, request: { ...body.request, revision: 'c'.repeat(40) } });
      case '/api/evals/start': return reply({ run_id: 'fixture-micro-tier' });
      case '/api/evals/result': return reply(body.run_id === 'fixture-micro-tier'
        ? { schema_version: 1, analysis_error: null, result: { ...states.paidAnalysis, result: fixtures.unit },
            run: { run_id: 'fixture-micro-tier', status: 'succeeded', suite: 'micro-tier' } }
        : { schema_version: 1, analysis_error: null, result: fixtures.hook,
            run: { run_id: 'fixture-hook-matrix', status: 'succeeded', suite: 'hook-matrix' } });
      case '/api/experiments/native-acceptance/catalog': return reply(states.nativeCatalog);
      case '/api/experiments/native-acceptance/preview': return reply(states.nativePreview);
      case '/api/experiments/native-acceptance/start': return reply(states.nativeRun);
      case '/api/experiments/native-acceptance/progress': return reply(states.nativeSnapshot);
      case '/api/runs/detail': if (body.run_id === states.runDetail.run_id) return reply({ ...states.runDetail,
        engine_reports: { ...states.runDetail.engine_reports, scorecard: fixtures.definitive.scorecard, judge: fixtures.definitive.judge } }); break;
      case '/api/runs/evidence': return reply(states.runEvidence);
      case '/api/configure/test/verdicts': return reply(fixtures.draftTest);
      case '/api/configure/apply/review': return reply({ ...states.applyReview,
        draft: { ...states.applyReview.draft, name: body.draft, revision: globalThis.__a11yRevision },
        apply_command: 'citizen draft apply ' + body.draft + ' --revision ' + globalThis.__a11yRevision + ' --json' });
      case '/api/configure/apply': return reply({ ...states.applyResult, message: 'Applied draft ' + body.draft + '.' });
      case '/api/configure/apply/rollback/preview': return reply(states.rollbackPreview);
      // A completed rollback reloads the timeline at once, so its result view never stays on screen;
      // the engine's busy refusal stays, and is the result view the audit can read.
      case '/api/configure/apply/rollback': return reply(states.rollbackRefused);
    }
    if (url === '/api/activity') return reply(states.activity);
    if (method !== 'GET' && !READS.has(url)) {
      globalThis.__a11yRefused.push(method + ' ' + url);
      return Promise.resolve(new Response('{"error":"fixture_only"}', { status: 409 }));
    }
    return original(input, init);
  };
})();
"""
PHONE = 320
# The UX specification's phone width; 320 is the reflow floor below it.
WIDE_PHONE = 390
DESKTOP = 1280
WIDTHS = (PHONE, WIDE_PHONE, DESKTOP)

# The controls a Tab walk must reach: one stop per radio group, since Tab enters a group once and
# the arrows move within it. Each is marked so the walk can say which control it never reached.
MARK_EXPECTED = """
(() => {
  document.querySelectorAll('[data-a11y-expected]').forEach(node => delete node.dataset.a11yExpected);
  const groups = new Set();
  const nodes = [...document.querySelectorAll('a[href], button:not([disabled]),'
    + ' input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary,'
    + ' [tabindex]')].filter(node => node.tabIndex >= 0 && node.checkVisibility({ visibilityProperty: true })
    && !node.closest('[inert], [aria-hidden=true], fieldset[disabled]'))
    .filter(node => node.type !== 'radio' || !groups.has(node.name) && groups.add(node.name));
  nodes.forEach(node => { node.dataset.a11yExpected = node.type === 'radio' ? 'radio:' + node.name : '1'; });
  return nodes.length;
})()
"""

# Each control's look before it takes focus, so a focused state is judged against its own
# unfocused state: a shadow a control always wears is not a focus indicator.
BASELINE = """
(() => {
  globalThis.__a11ySnap = (node) => {
    const style = getComputedStyle(node);
    return { outline: [style.outlineStyle, style.outlineWidth, style.outlineColor, style.outlineOffset].join(' '),
      shadow: style.boxShadow };
  };
  globalThis.__a11yBase = new WeakMap();
  document.querySelectorAll('[data-a11y-expected], [tabindex], .mantine-Input-input').forEach(node => __a11yBase.set(node, __a11ySnap(node)));
})()
"""

# Expected controls the walk never focused, a radio group counting as reached through any member.
UNREACHED = r"""
(() => {
  const visited = (node) => node.dataset.a11yExpected.startsWith('radio:')
    ? [...document.querySelectorAll('input[type=radio]')].some(radio => radio.name === node.name && radio.dataset.a11yVisit)
    : Boolean(node.dataset.a11yVisit);
  return JSON.stringify([...document.querySelectorAll('[data-a11y-expected]')].filter(node => !visited(node))
    .map(node => node.tagName.toLowerCase() + (node.id ? '#' + node.id : '') + ' "'
      + (node.getAttribute('aria-label') || node.innerText || node.value || '').trim().replace(/\s+/g, ' ').slice(0, 50) + '"'));
})()
"""

# Settled: no loader or busy region, a level-one heading, and the text unchanged across polls.
# The page's text and the colours its headings and paragraphs resolve to must hold across two polls,
# so a theme switch that lands after the route renders is waited out too.
SETTLED = ("(() => { const busy = document.querySelector('[aria-busy=true], .mantine-Loader-root');"
           " const paint = [...document.querySelectorAll('main h1, main h2, main p')].slice(0, 40)"
           ".map(node => getComputedStyle(node).color).join();"
           " const text = document.documentElement.dataset.mantineColorScheme + paint + document.body.innerText;"
           " const stable = text === globalThis.__a11yText;"
           " globalThis.__a11yText = text;"
           " const heading = document.querySelector('main h1');"
           " return !busy && heading !== null && !heading.dataset.a11yStale && stable; })()")
# Before a route change: the old page's heading is marked and the remembered text dropped, so a
# poll that still sees the previous page cannot read as settled.
LEAVE = ("(() => { globalThis.__a11yText = null;"
         " document.querySelectorAll('main h1').forEach(node => { node.dataset.a11yStale = '1'; }); })()")

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
  // A Mantine field's typing element can be a sliver inside the box the user sees; that box
  // carries the ring, so it is what is judged.
  const box = element.closest('.mantine-Input-input') || element;
  const style = getComputedStyle(box);
  const rect = box.getBoundingClientRect();
  const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
  // The ring is measured against what it paints over: a point in the middle of its band, which
  // for an inset ring is the control's own content.
  const ringOffset = parseFloat(style.outlineOffset) || 0;
  const ringWidth = parseFloat(style.outlineWidth) || 0;
  const under = document.elementFromPoint(rect.left - ringOffset - ringWidth / 2, rect.top + rect.height / 2);
  const ringBackdrop = backdrop(under && under !== element ? under : box.parentElement);
  const outlineContrast = outline ? ratio(parse(style.outlineColor), ringBackdrop) : 0;
  // The ring is the band between the box grown by the offset and grown again by the width. Every
  // ancestor that clips its overflow cuts it to its padding box; under half left is not visible.
  const grow = (by) => ({ l: rect.left - by, t: rect.top - by, r: rect.right + by, b: rect.bottom + by });
  const meet = (a, b) => ({ l: Math.max(a.l, b.l), t: Math.max(a.t, b.t), r: Math.min(a.r, b.r), b: Math.min(a.b, b.b) });
  const area = (box) => Math.max(0, box.r - box.l) * Math.max(0, box.b - box.t);
  const offset = parseFloat(style.outlineOffset) || 0;
  const outer = grow(offset + (parseFloat(style.outlineWidth) || 0));
  const inner = grow(offset);
  let clip = { l: -Infinity, t: -Infinity, r: Infinity, b: Infinity };
  let clipper = '';
  for (let node = box.parentElement; node && node !== document.body; node = node.parentElement) {
    const own = getComputedStyle(node);
    if (own.overflowX === 'visible' && own.overflowY === 'visible') continue;
    const box = node.getBoundingClientRect();
    const left = box.left + node.clientLeft;
    const top = box.top + node.clientTop;
    const next = meet(clip, { l: left, t: top, r: left + node.clientWidth, b: top + node.clientHeight });
    if (area(meet(outer, next)) < area(meet(outer, clip))) {
      clipper = clipper || node.tagName.toLowerCase() + '.' + String(node.className).trim().split(/\s+/).slice(0, 2).join('.');
    }
    clip = next;
  }
  const band = area(outer) - area(inner);
  const painted = area(meet(outer, clip)) - area(meet(inner, clip));
  const clipped = outline && band > 0 && painted < band / 2;
  // Focus must change the control's look: compared against the control's own unfocused state.
  const base = globalThis.__a11yBase && globalThis.__a11yBase.get(box);
  const now = globalThis.__a11ySnap ? globalThis.__a11ySnap(box) : { outline: '', shadow: style.boxShadow };
  const outlineShown = outline && outlineContrast >= 3 && !clipped && (!base || base.outline !== now.outline);
  // A shadow that appears on focus counts only when its colour reaches 3:1 against the surface.
  const shadowColor = parse((now.shadow.match(/rgba?\([^)]*\)/) || [''])[0]);
  const shadowContrast = shadowColor.length >= 3 && (shadowColor.length === 3 || shadowColor[3] === 1)
    ? ratio(shadowColor, backdrop(box.parentElement)) : 0;
  const ringShown = Boolean(base) && now.shadow !== 'none' && base.shadow !== now.shadow && shadowContrast >= 3;
  const points = [[rect.left + rect.width / 2, rect.top + rect.height / 2],
    [rect.left + 2, rect.top + 2], [rect.right - 2, rect.top + 2],
    [rect.left + 2, rect.bottom - 2], [rect.right - 2, rect.bottom - 2]];
  let cover = '';
  const covered = points.every(([x, y]) => {
    const hit = document.elementFromPoint(x, y);
    const hides = hit && !element.contains(hit) && !hit.contains(element);
    if (hides && !cover) cover = hit.tagName.toLowerCase() + '.' + String(hit.className).trim().split(/\s+/).slice(0, 2).join('.');
    return hides;
  });
  const offscreen = rect.bottom < 0 || rect.top > innerHeight || rect.width === 0;
  globalThis.__a11yVisits = globalThis.__a11yVisits || 0;
  const text = (element.getAttribute('aria-label') || element.innerText || element.value || '')
    .trim().replace(/\s+/g, ' ').slice(0, 50);
  return JSON.stringify({
    node: element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') + ' "' + text + '"',
    revisit: element.dataset.a11yVisit || null,
    visit: element.dataset.a11yVisit || (element.dataset.a11yVisit = String(++globalThis.__a11yVisits)),
    visible: outlineShown || ringShown,
    outline: style.outlineStyle + ' ' + style.outlineWidth + ' ' + style.outlineColor + ' ' + outlineContrast.toFixed(2)
      + (clipped ? ' clipped to ' + Math.round(100 * painted / band) + '% by ' + clipper : '')
      + (base && !outlineShown && !ringShown ? ' unchanged from the unfocused state' : ''),
    obscured: covered && !offscreen,
    cover,
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
        # The studio-e2e job opts in: there a missing Chrome fails instead of skipping.
        if e2e_support.opted_in():
            e2e_support.chrome_or_fail(self)
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
        # Navigating to `#/` from another route stays in the page, so the old page is marked first.
        try:
            self.devtools.evaluate(LEAVE)
        except RuntimeError:
            pass
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

    def _install_stubs(self, revision: str = "") -> None:
        # Installed into the open page, which hash routing keeps: the hook-matrix fixture is larger
        # than one DevTools frame carries, so the fixtures go over in pieces.
        self.devtools.evaluate(e2e_support.PAGE_HELPERS)
        files = (("compare", "compare.json"), ("hook", "eval-hook-matrix.json"), ("replay", "replay-analysis.json"),
                 ("unit", "eval-unit-analysis.json"), ("draftTest", "draft-test.json"), ("states", "a11y-states.json"),
                 ("definitive", "definitive-evaluation.json"))
        self.devtools.evaluate("globalThis.__a11yFixtures = %s; globalThis.__a11yRevision = %s" % (
            json.dumps({name: "" for name, _ in files}), json.dumps(revision)))
        for name, file in files:
            text = (FIXTURES / file).read_text(encoding="utf-8")
            for start in range(0, len(text), 20000):
                self.devtools.evaluate("__a11yFixtures.%s += %s" % (name, json.dumps(text[start:start + 20000])))
        self.devtools.evaluate(STUBS)

    def _assert_nothing_refused(self) -> None:
        self.assertEqual(json.loads(self.devtools.evaluate("JSON.stringify(globalThis.__a11yRefused)")), [])

    def _set_label(self, label: str, value: str, within: str = "document") -> None:
        find = ("(() => { const root = %s; const label = [...root.querySelectorAll('label')]"
                ".find(item => item.textContent.trim().startsWith(%s));"
                " return label && (document.getElementById(label.htmlFor) || label.closest('div').querySelector('input, textarea')); })()"
                % (within, json.dumps(label)))
        self._wait("%s !== null && %s !== undefined" % (find, find), "%s did not render" % label)
        self.devtools.evaluate(
            "(input => { Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement"
            " ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, %s);"
            " input.dispatchEvent(new Event('input', { bubbles: true })); })(%s)" % (json.dumps(value), find))
        self._wait("%s.value === %s" % (find, json.dumps(value)), "%s did not take its value" % label)

    def _click(self, text: str, seconds: float = 20.0) -> None:
        self._wait("__buttonReady(%s)" % json.dumps(text), "%s never became ready" % text, seconds)
        self.devtools.evaluate("__click(%s)" % json.dumps(text))

    def _audit_into(self, failures: List[str], label: str) -> None:
        self._wait(SETTLED, "%s did not settle" % label, seconds=30.0)
        result = self._audit()
        self.assertGreater(result["checked"]["text"], 0, label)
        for violation in result["violations"]:
            failures.append("%s [%s] %s: %s" % (label, violation["rule"], violation["node"], violation["detail"]))

    def _visit(self, route: str, scheme: str) -> None:
        if self.devtools.evaluate("location.hash") != "#" + route:
            self.devtools.evaluate(LEAVE)
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

    def _tab_walk(self, route: str) -> List[str]:
        """Walk ``Tab`` from the skip link to the end of the page; return what a keyboard user would hit."""
        failures: List[str] = []
        focusable = self.devtools.evaluate(MARK_EXPECTED)
        self.devtools.evaluate(
            "document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0);"
            " globalThis.__a11yVisits = 0;"
            " document.querySelectorAll('[data-a11y-visit]').forEach(node => delete node.dataset.a11yVisit)")
        self.devtools.evaluate(BASELINE)
        self.devtools.evaluate("document.querySelector('.skip-link').focus()")
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
                failures.append("%s [focus-not-obscured] %s under %s" % (route, state["node"], state["cover"]))
        if not left_page:
            failures.append("%s [keyboard-trap] Tab never left the page after %d presses" % (route, len(seen)))
        # Reach compares sets: an extra stop must not stand in for a control never reached.
        for node in json.loads(self.devtools.evaluate(UNREACHED)):
            failures.append("%s [keyboard] Tab never reached %s (walked: %s)" % (route, node, " > ".join(seen)))
        return failures

    def _report(self, failures: List[str]) -> None:
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    # Tests.
    def test_every_route_meets_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        failures: List[str] = []
        audits = 0
        for width in WIDTHS:
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
        self.assertEqual(audits, len(ROUTES) * len(WIDTHS) * 2)
        self._report(failures)

    def test_populated_reports_meet_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        self._populate()
        failures: List[str] = []
        for width in WIDTHS:
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                for route in POPULATED:
                    self._visit(route, scheme)
                    self.assertFalse(self.devtools.evaluate("document.querySelector('[role=alert]') !== null"),
                                     "%s did not render its fixture" % route)
                    # Chart labels stay readable: at least 11 px as drawn, at every width.
                    small = self.devtools.evaluate(
                        "[...document.querySelectorAll('.trend-chart text')].map(text =>"
                        " text.getScreenCTM().a * parseFloat(getComputedStyle(text).fontSize))"
                        ".filter(size => size < 11).length")
                    if small:
                        failures.append("%s populated @%dpx %s [chart-label-size] %d labels under 11 px" % (
                            route, width, scheme, small))
                    result = self._audit()
                    self.assertGreater(result["checked"]["text"], 0, route)
                    for violation in result["violations"]:
                        failures.append("%s populated @%dpx %s [%s] %s: %s" % (
                            route, width, scheme, violation["rule"], violation["node"],
                            violation["detail"]))
                    if width == PHONE and scheme == "light":
                        failures.extend(self._tab_walk(route + " populated"))
        self._report(failures)

    def test_every_rule_fires_on_a_planted_violation(self):
        # Proves the rule set bites: each planted defect is reported by its own rule, on the
        # planted node where the rule names one. Page-wide rules are planted on the document and
        # restored, and the clean audit afterwards proves every plant was the cause.
        self._open(PHONE, "light")
        self._visit("/", "light")
        self.devtools.evaluate(r"""(() => {
          const main = document.querySelector('main');
          const planted = document.createElement('div');
          planted.id = 'planted';
          planted.innerHTML = `
            <h1>Planted second title</h1>
            <h4>Skipped heading level</h4>
            <h2 id="planted-empty-heading"></h2>
            <p style="color:#bbbbbb;background:#ffffff">Faint text</p>
            <p style="background-image:linear-gradient(#ffffff,#eeeeee)">Gradient text</p>
            <svg width="160" height="20"><text x="0" y="15" style="fill:#cccccc">Faint chart label</text></svg>
            <input id="planted-faint-value" aria-label="Faint value" value="Faint typed value" style="color:#bbbbbb;background:#ffffff;border:1px solid #333333">
            <input id="planted-faint-placeholder" aria-label="Faint placeholder" placeholder="Faint hint">
            <button aria-label="Launch">Start run</button>
            <label for="planted-labelled">Draft name</label><input id="planted-labelled" aria-label="Name">
            <button></button>
            <a href="#/" style="display:inline-block;width:10px;height:10px;overflow:hidden">x</a><a href="#/" style="display:inline-block;width:10px;height:10px;overflow:hidden">y</a>
            <input aria-label="Faint field" style="border:1px solid #f4f4f4;background:#ffffff">
            <input id="planted-gradient-field" aria-label="Gradient field" style="background-image:linear-gradient(#ffffff,#eeeeee)">
            <span aria-labelledby="missing-id">Dangling reference</span>
            <span id="planted-dup">One</span><span id="planted-dup">Two</span><span aria-describedby="planted-dup">Duplicate reference</span>
            <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
            <div aria-hidden="true"><button>Hidden button</button></div>
            <button tabindex="2">Positive tabindex</button>
            <button>Outer control <a href="#/">inner link</a></button>
            <div role="progressbar" aria-valuenow="5" style="width:60px;height:8px"></div>
            <ul><div>Not an item</div></ul>
            <nav></nav><nav></nav>
            <div style="overflow:auto;width:100px;height:40px"><div style="width:400px;height:80px">Unreachable scroller</div></div>
            <div tabindex="0" style="overflow:auto;width:100px;height:40px"><div style="width:400px;height:80px">Nameless scroller</div></div>
            <div tabindex="0" aria-label="Roleless" style="overflow:auto;width:100px;height:40px"><div style="width:400px;height:80px">Roleless scroller</div></div>
            <svg width="160" height="20"><text x="0" y="15" style="fill:#182b2d;fill-opacity:0.3">Translucent chart label</text></svg>
            <div style="width:900px">Too wide</div>`;
          main.appendChild(planted);
          const sheet = new CSSStyleSheet();
          sheet.replaceSync('#planted-faint-placeholder::placeholder { color: #cccccc; opacity: 1; }');
          globalThis.__plantedSheets = document.adoptedStyleSheets;
          document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
          const stray = document.createElement('p');
          stray.id = 'planted-stray';
          stray.textContent = 'Outside every landmark';
          document.querySelector('.studio-frame').appendChild(stray);
          const second = document.createElement('main');
          second.id = 'planted-main';
          document.body.appendChild(second);
          const banner = document.createElement('header');
          banner.id = 'planted-banner';
          document.body.appendChild(banner);
          globalThis.__plantedTitle = document.title;
          document.title = '';
          document.documentElement.removeAttribute('lang');
        })()""")
        try:
            violations = self._audit()["violations"]
        finally:
            self.devtools.evaluate(
                "['planted', 'planted-stray', 'planted-main', 'planted-banner']"
                ".forEach(id => document.getElementById(id).remove());"
                " document.title = globalThis.__plantedTitle; document.documentElement.lang = 'en';"
                " document.adoptedStyleSheets = globalThis.__plantedSheets")
        # (rule, text the reported node must contain); None for a rule reported on the document.
        expected = [
            ("html-lang", None), ("document-title", None), ("landmark-main", None),
            ("landmark-banner", None), ("landmark-unique", None), ("page-has-one-h1", None),
            ("heading-order", "Skipped heading level"), ("empty-heading", "#planted-empty-heading"),
            ("color-contrast", "Faint text"), ("color-contrast", "Faint chart label"),
            ("color-contrast", "#planted-faint-value"), ("color-contrast", "#planted-faint-placeholder"),
            ("color-contrast-undecided", "Gradient text"),
            ("non-text-contrast", "input"), ("non-text-contrast-undecided", "#planted-gradient-field"),
            ("label-in-name", "Start run"), ("label-in-name", "#planted-labelled"),
            ("control-name", "button"), ("target-size", '"x"'),
            ("aria-valid-reference", "Dangling reference"), ("duplicate-id-aria", "Duplicate reference"),
            ("image-alt", "img"), ("aria-hidden-focus", "Hidden button"), ("tabindex", "Positive tabindex"),
            ("nested-interactive", "Outer control"), ("progressbar-name", "div"), ("list", "Not an item"),
            ("scrollable-region-focusable", "Unreachable scroller"),
            ("scrollable-region-name", "Nameless scroller"), ("scrollable-region-name", "Roleless scroller"),
            ("color-contrast", "Translucent chart label"), ("reflow", None),
            ("region", "Outside every landmark"),
        ]
        missing = [(rule, marker) for rule, marker in expected
                   if not any(item["rule"] == rule and (marker is None or marker in item["node"])
                              for item in violations)]
        self.assertEqual(missing, [], json.dumps(violations, indent=1))
        # Every rule the checker can report is planted above.
        reported = set(re.findall(r'add\("([a-z-]+)"', CHECKS))
        self.assertEqual(reported - {rule for rule, _ in expected}, set())
        self.assertEqual(self._audit()["violations"], [])

    def test_every_keyboard_rule_fires_on_a_planted_violation(self):
        # The Tab walk's own rules, each proven on a planted control: a ring an ancestor clips
        # away, a static shadow that never changes on focus, a control covered when focused, a
        # control Tab never reaches, and a control that keeps Tab.
        self._open(PHONE, "light")
        self._visit("/", "light")
        self.devtools.evaluate(r"""(() => {
          const planted = document.createElement('div');
          planted.id = 'planted';
          planted.innerHTML = `
            <div style="overflow:hidden;width:200px;height:44px"><div id="planted-clipped" role="region" aria-label="Clipped" tabindex="0" style="width:200px;height:44px">Clipped ring</div></div>
            <button id="planted-shadow" style="outline:none !important;box-shadow:0 1px 3px #000000">Static shadow</button>
            <button id="planted-skipped">Skipped</button>
            <div style="position:relative"><button id="planted-covered">Covered</button><div style="position:absolute;inset:0;z-index:5;background:#ffffff"></div></div>`;
          document.querySelector('main').appendChild(planted);
          const covered = document.getElementById('planted-covered');
          document.getElementById('planted-skipped').addEventListener('focus', () => covered.focus());
        })()""")
        try:
            failures = self._tab_walk("planted")
        finally:
            self.devtools.evaluate("document.getElementById('planted').remove()")
        expected = [("focus-visible", "#planted-clipped"), ("focus-visible", "#planted-shadow"),
                    ("focus-not-obscured", "#planted-covered"), ("keyboard", "#planted-skipped")]
        missing = [(rule, marker) for rule, marker in expected
                   if not any(("[%s]" % rule) in line and marker in line.split("(walked:")[0] for line in failures)]
        self.assertEqual(missing, [], "\n".join(failures))
        self.assertFalse([line for line in failures if "#planted" not in line.split("(walked:")[0]],
                         "\n".join(failures))

        self.devtools.evaluate(r"""(() => {
          const trap = document.createElement('button');
          trap.id = 'planted-trap';
          trap.textContent = 'Keeps Tab';
          trap.addEventListener('keydown', (event) => { if (event.key === 'Tab') event.preventDefault(); });
          document.querySelector('main').prepend(trap);
        })()""")
        try:
            failures = self._tab_walk("trap")
        finally:
            self.devtools.evaluate("document.getElementById('planted-trap').remove()")
        self.assertTrue(any("[keyboard-trap] Tab stays on button#planted-trap" in line for line in failures),
                        "\n".join(failures))
        self.assertEqual(self._tab_walk("/"), [])

    def test_populated_experiment_panels_meet_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        # Compare, the hook matrix, a paid tier's analysis, native acceptance progress and a live
        # replay with its progress and result tables, each showing its committed fixture.
        failures: List[str] = []
        for width in WIDTHS:
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                self._install_stubs()
                self._visit("/experiments", scheme)
                label = "/experiments populated @%dpx %s" % (width, scheme)
                self._fill_experiments(failures, label)
                self._audit_into(failures, label)
                if width == PHONE and scheme == "light":
                    failures.extend(self._tab_walk("/experiments populated"))
                self._assert_nothing_refused()
        self._report(failures)

    def _fill_experiments(self, failures: List[str], label: str) -> None:
        self._wait("__has('Compare two runs, paired by task.')", "the compare panel did not render")
        for field in ("Base run id", "Candidate run id"):
            self._set_label(field, "00000000-0000-4000-8000-000000000011")
        self._click("Compare")
        self._wait("__has('Compared by the engine.')", "the compare fixture did not render")

        self._click("Run the hook matrix")
        self._wait("document.querySelector('[aria-label^=\"Hook matrix for \"]') !== null",
                   "the hook matrix fixture did not render")

        tiers = "[...document.querySelectorAll('h2')].find(h => h.textContent === 'Run an engine, read its own result.').closest('.mantine-Paper-root')"
        self._set_label("Target reference", "fixture-branch", within=tiers)
        self._set_label("Per-run cap (USD)", "0.50", within=tiers)
        self._set_label("Spend cap (USD)", "2", within=tiers)
        self.devtools.evaluate("[...(%s).querySelectorAll('button')].find(b => b.textContent === 'Preview spend').click()" % tiers)
        self._click("Confirm and spend up to 2 USD")
        self._wait("__has('cost_bench.py summarise')", "the paid tier analysis fixture did not render")

        self._click("Review spend and run")
        self._click("Confirm and launch")
        self._wait("document.querySelector('[aria-label=\"Settled native acceptance cases\"]') !== null"
                   " && __has('wrong stance')", "the native acceptance progress fixture did not render")

        self._set_label("Target 1 reference", "v0.17.0")
        self._set_label("Target 2 reference", "fixture-draft")
        self._set_label("Pre-registration", "fixture pre-registration")
        if not self.devtools.evaluate("__labelled('Model').value"):
            self._set_label("Model", "claude-test")
        tasks = ("document.getElementById(__labelled('Tasks').getAttribute('aria-controls'))")
        self.devtools.evaluate("__labelled('Tasks').click()")
        self._wait("(%s)?.querySelector('[role=option]') != null" % tasks, "Tasks listed no options")
        self.devtools.evaluate("(%s).querySelector('[role=option]').click()" % tasks)
        self.devtools.evaluate("document.activeElement && document.activeElement.blur()")
        replay = "[...document.querySelectorAll('h2')].find(h => h.textContent === 'Measure two explicit targets.').parentElement.parentElement"
        self._wait("[...(%s).querySelectorAll('button')].some(b => b.textContent === 'Preview spend' && !b.disabled)" % replay,
                   "the replay preview never became ready")
        self.devtools.evaluate("[...(%s).querySelectorAll('button')].find(b => b.textContent === 'Preview spend').click()" % replay)
        self._wait("__has('Exploratory: a task subset') && __has('aaaaaaaaaaaa')",
                   "the replay preview's sampling and resolved revisions did not render")
        # The preview gives way to the run once it starts, so it is audited here.
        self._audit_into(failures, label + " replay preview")
        self._click("Confirm and run")
        self._wait("__has('Engine analysis, target ') && document.querySelector('[aria-label=\"Live replay progress\"]') !== null"
                   " && document.querySelector('[aria-label=\"Cost and pass rate\"]') !== null",
                   "the replay fixtures did not render")
        self._wait(SETTLED, "the populated experiments did not settle", seconds=30.0)

    def test_a_populated_run_page_meets_the_aa_rules_at_phone_and_desktop_width_in_both_themes(self):
        # The run page with its exact command, cases and case evidence, and the definitive
        # evaluation's engine row, layer scorecard and judge: every scroll region it has.
        states = json.loads((FIXTURES / "a11y-states.json").read_text(encoding="utf-8"))
        route = "/experiments/runs/" + states["runDetail"]["run_id"]
        failures: List[str] = []
        for width in WIDTHS:
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                self._install_stubs()
                self._visit(route, scheme)
                self._click("Standard output")
                self._wait("['Exact command', 'Cases', 'Case evidence', 'Engine row fields', 'Layer scorecard',"
                           " 'Diff-quality judge'].every(name =>"
                           " document.querySelector('[role=region][aria-label=\"' + name + '\"]') !== null)",
                           "the run page's scroll regions did not render")
                self._wait("__has('Engine report unavailable')", "the engine report error state did not render")
                self._audit_into(failures, "%s @%dpx %s" % (route, width, scheme))
                if width == PHONE and scheme == "light":
                    failures.extend(self._tab_walk(route))
                self._assert_nothing_refused()
        self._report(failures)

    def test_a_loaded_draft_reviewed_and_applied_meets_the_aa_rules_at_phone_and_desktop_width(self):
        # Configure with a real draft loaded, its draft-test verdicts, then the apply review and
        # the apply result from the stub; the review and apply never reach the engine.
        draft = draft_support.draft_name("a11y-browser-")
        # The suite's isolation sets HARNESS_QUIET, which silences --json; this call reads it.
        loud = {key: value for key, value in self.env.items() if key != "HARNESS_QUIET"}
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", draft, "--json"],
            env=loud, capture_output=True, text=True, timeout=30)
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        draft_support.register_draft_cleanup(self, draft, self.env, stop=self._stop_studio)
        described = json.loads(created.stdout)
        revision = described.get("revision") or described.get("draft", {}).get("revision")
        self.assertTrue(revision, created.stdout)
        failures: List[str] = []
        for width in WIDTHS:
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                self._install_stubs(revision)
                self._visit("/configure", scheme)
                self._set_label("Draft name", draft)
                self._click("Load draft")
                self._wait("__has('Draft configuration is ready.')", "draft configuration did not load", seconds=60.0)
                self._wait(SETTLED, "the loaded draft did not settle", seconds=30.0)
                label = "draft @%dpx %s" % (width, scheme)
                self._audit_into(failures, label + " loaded")
                self._click("Review draft")
                self._wait("__has('Review complete. Nothing has been applied.')", "the apply review fixture did not render")
                self._audit_into(failures, label + " reviewed")
                if width == PHONE and scheme == "light":
                    failures.extend(self._tab_walk("/configure reviewed"))
                self._set_label("Confirm the draft to apply", draft)
                self._click("Apply " + draft)
                self._wait("__has(%s)" % json.dumps("Applied draft %s." % draft), "the apply result fixture did not render")
                self._audit_into(failures, label + " applied")
                self._assert_nothing_refused()
        self._report(failures)

    def test_a_rollback_previewed_and_done_meets_the_aa_rules_at_phone_and_desktop_width(self):
        failures: List[str] = []
        for width in WIDTHS:
            for scheme in ("light", "dark"):
                self._open(width, scheme)
                self._install_stubs()
                self._visit("/activity", scheme)
                label = "/activity @%dpx %s" % (width, scheme)
                self._click("Preview rollback")
                self._wait("__has('Preview ready. Nothing has changed.')", "the rollback preview fixture did not render")
                self._audit_into(failures, label + " rollback preview")
                if width == PHONE and scheme == "light":
                    failures.extend(self._tab_walk("/activity rollback preview"))
                self._set_label("Confirm the applied draft to roll back", "tuning")
                self._click("Roll back tuning")
                self._wait("__has('citizen sync (pid 1) holds the sync lock') && __has('nothing was changed')",
                           "the rollback result fixture did not render")
                self._audit_into(failures, label + " rollback refused")
                self._assert_nothing_refused()
        self._report(failures)

    def test_clicks_pass_through_the_toast_region_but_not_a_toast(self):
        # The region spans the bottom of the page; only a toast in it takes the pointer.
        self._open(DESKTOP, "light")
        self._visit("/", "light")
        probe = json.loads(self.devtools.evaluate(r"""(() => {
          const region = document.querySelector('.toast-region');
          const toasts = [0, 1].map(() => {
            const toast = document.createElement('div');
            toast.className = 'studio-toast planted-toast';
            toast.textContent = 'Planted toast';
            region.appendChild(toast);
            return toast;
          });
          const first = toasts[0].getBoundingClientRect();
          const second = toasts[1].getBoundingClientRect();
          const x = first.left + first.width / 2;
          const gap = document.elementFromPoint(x, (first.bottom + second.top) / 2);
          const onToast = document.elementFromPoint(x, first.top + first.height / 2);
          const result = { gapHeight: second.top - first.bottom, gapIsRegion: gap === region,
            gapReachesPage: Boolean(gap) && !region.contains(gap), toastTakesPointer: toasts[0].contains(onToast) };
          toasts.forEach(toast => toast.remove());
          return JSON.stringify(result);
        })()"""))
        self.assertGreater(probe["gapHeight"], 0, probe)
        self.assertFalse(probe["gapIsRegion"], probe)
        self.assertTrue(probe["gapReachesPage"], probe)
        self.assertTrue(probe["toastTakesPointer"], probe)

    def test_tab_reaches_every_route_control_with_visible_focus_and_no_trap(self):
        # At the narrowest width in the light theme, and at desktop width in the dark theme.
        failures: List[str] = []
        for width, scheme in ((PHONE, "light"), (DESKTOP, "dark")):
            self._open(width, scheme)
            for route in ROUTES:
                self._visit(route, scheme)
                failures.extend(self._tab_walk("%s @%dpx %s" % (route, width, scheme)))
        self._report(failures)

if __name__ == "__main__":
    unittest.main()
