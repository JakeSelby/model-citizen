# SPDX-License-Identifier: MIT
"""Keyboard, theme, clipboard, and contrast checks for the shared Studio kit."""
import json
import subprocess
import time
import unittest

import test_studio_browser as browser_support
import test_studio_configure_browser as configure_support


class StudioKitBrowserTests(unittest.TestCase):
    setUp = configure_support.ConfigureBrowserTests.setUp
    tearDown = configure_support.ConfigureBrowserTests.tearDown
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _wait = configure_support.ConfigureBrowserTests._wait
    _open_configure = configure_support.ConfigureBrowserTests._open_configure
    _load_draft = configure_support.ConfigureBrowserTests._load_draft

    def test_exact_command_copy_success_failure_and_dismiss(self):
        self._open_configure("""
            globalThis.__copied = null;
            globalThis.__clipboardFails = false;
            Object.defineProperty(navigator, 'clipboard', {value: {writeText: async (text) => {
              if (__clipboardFails) throw new Error('denied');
              globalThis.__copied = text;
            }}});
        """)
        self._load_draft(self.draft)
        command = self.devtools.evaluate("document.querySelector('.command-chip code').textContent")
        self.assertTrue(command.startswith("citizen "), command)
        self.devtools.evaluate("document.querySelector('.command-chip button').click()")
        self._wait("document.querySelector('.studio-toast') !== null", "copy feedback missing")
        self.assertEqual(self.devtools.evaluate("__copied"), command)
        self.assertIn("Command copied", self.devtools.evaluate("document.querySelector('.studio-toast').textContent"))
        self.devtools.evaluate("document.querySelector('.studio-toast button').click()")
        self._wait("document.querySelector('.studio-toast') === null", "toast did not dismiss")
        self.devtools.evaluate("__clipboardFails = true; document.querySelector('.command-chip button').click()")
        self._wait("document.querySelector('.studio-toast[data-tone=danger]') !== null", "copy failure missing")
        self.assertEqual(self.devtools.evaluate("document.querySelector('.command-chip code').textContent"), command)

    def test_themes_contrast_focus_and_narrow_content(self):
        self._open_configure("")
        self.devtools.evaluate("location.hash = '#/'")
        self._wait("document.querySelector('.overview-card') !== null", "hub missing")
        for theme in ("light", "dark"):
            self.devtools.evaluate("""(() => {
                const select = document.querySelector('select[aria-label="Color theme"]');
                select.value = %s;
                select.dispatchEvent(new Event('change', {bubbles:true}));
            })()""" % json.dumps(theme))
            self._wait("document.documentElement.dataset.mantineColorScheme === " + json.dumps(theme), "theme did not change")
            colors = self.devtools.evaluate("""(() => {
              const root = getComputedStyle(document.documentElement);
              const resolve = (name) => {
                const probe = document.createElement('span'); probe.style.color = `var(${name})`;
                document.body.append(probe); const color = getComputedStyle(probe).color; probe.remove(); return color;
              };
              return ['primary','success','warning','danger'].map(tone => [resolve('--studio-'+tone), resolve('--studio-'+tone+'-wash')])
                .concat([['--studio-ink-secondary','--studio-surface-raised'], ['--studio-control-border','--studio-surface-raised']].map(pair => pair.map(resolve)));
            })()""")
            for index, (foreground, background) in enumerate(colors):
                self.assertGreaterEqual(browser_support._contrast_ratio(foreground, background), 3 if index == 5 else 4.5)
            control = self.devtools.evaluate("""(() => {
              const style = getComputedStyle(document.querySelector('select[aria-label="Color theme"]'));
              return {border:style.borderTopColor, background:style.backgroundColor, text:style.color};
            })()""")
            self.assertGreaterEqual(browser_support._contrast_ratio(control["border"], control["background"]), 3)
            self.assertGreaterEqual(browser_support._contrast_ratio(control["text"], control["background"]), 4.5)
            self.devtools.call("Emulation.setDeviceMetricsOverride", {"width": 320, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            self.devtools.evaluate("document.querySelector('.overview-lead').textContent = 'A'.repeat(120)")
            self.assertTrue(self.devtools.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            self.devtools.evaluate("document.querySelector('.skip-link').focus()")
            self.devtools.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
            focus = self.devtools.evaluate("""({tag:document.activeElement.tagName, width:getComputedStyle(document.activeElement).outlineWidth})""")
            self.assertEqual(focus["tag"], "A")
            self.assertNotEqual(focus["width"], "0px")
        preference_path = browser_support.state_root(self.home) / "ui-preferences.json"
        for _ in range(100):
            if preference_path.is_file() and json.loads(preference_path.read_text())["color_scheme"] == "dark":
                break
            time.sleep(0.02)
        self.assertTrue(preference_path.is_file())
        self.assertEqual(json.loads(preference_path.read_text())["color_scheme"], "dark")
        self.devtools.call("Page.reload")
        self._wait_for_shell()
        self._wait("!document.querySelector('select[aria-label=\"Color theme\"]')?.disabled",
                   "saved theme did not reload")
        self.assertEqual(self.devtools.evaluate("document.documentElement.dataset.mantineColorScheme"), "dark")

        stopped = subprocess.run(
            [browser_support.sys.executable, str(browser_support.CLI), "studio", "stop", "--json"],
            env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        launched = subprocess.run(
            [browser_support.sys.executable, str(browser_support.CLI), "studio", "--detach",
             "--no-open", "--json"], env=self.env, capture_output=True, text=True, timeout=8)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.started = json.loads(launched.stdout)
        record = json.loads((browser_support.state_root(self.home) / "instance.json").read_text())
        self.cookie = self._bootstrap(record)
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.navigate", {"url": self.started["url"]})
        self._wait_for_shell()
        self._wait("!document.querySelector('select[aria-label=\"Color theme\"]')?.disabled",
                   "saved theme did not load")
        self.assertEqual(
            self.devtools.evaluate("document.documentElement.dataset.mantineColorScheme"), "dark")

    def test_component_extremes_keep_badges_and_code_contained_at_320_pixels(self):
        self._open_configure("")
        markup = (browser_support.REPO / "studio/tests/fixtures/kit.html").read_text()
        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 320, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.devtools.evaluate("document.querySelector('main').innerHTML = " + json.dumps(markup))
        dimensions = self.devtools.evaluate("""({
          viewport: innerWidth, document: document.documentElement.scrollWidth,
          codes: [...document.querySelectorAll('.code-view')].map(node => ({client:node.clientWidth, scroll:node.scrollWidth})),
          badges: [...document.querySelectorAll('.data-table .status-badge')].map(node => ({whiteSpace:getComputedStyle(node).whiteSpace,height:node.getBoundingClientRect().height})),
        })""")
        self.assertLessEqual(dimensions["document"], dimensions["viewport"])
        for code in dimensions["codes"]:
            self.assertLessEqual(code["scroll"], code["client"] + 1)
        for badge in dimensions["badges"]:
            self.assertEqual(badge["whiteSpace"], "nowrap")
            self.assertLessEqual(badge["height"], 30)


if __name__ == "__main__":
    unittest.main()
