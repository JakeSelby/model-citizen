// SPDX-License-Identifier: MIT
// Hand-written WCAG 2.2 AA checks for the rendered Studio, evaluated in Chrome through DevTools.
// axe-core is MPL-2.0 and the repository takes no copyleft dependency, so these rules stand in
// for its AA rule set where a check can be decided from the DOM and computed styles. The
// expression returns a JSON string: {"violations": [{rule, node, detail}], "checked": {...}}.
// A check that cannot decide reports a `-undecided` rule rather than passing by being skipped.
(() => {
  const violations = [];
  const checked = { text: 0, fields: 0, controls: 0, targets: 0 };
  const LARGE_TEXT_PX = 24;
  const LARGE_BOLD_TEXT_PX = 18.66;
  const TARGET_PX = 24;
  const INTERACTIVE = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=switch]",
    "[role=tab]", "[role=menuitem]", "[role=option]", "[role=combobox]", "[role=slider]",
    "[role=textbox]", "[role=searchbox]", "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const NAME_FROM_CONTENT = "a[href], button, summary, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=switch]";

  function describe(element) {
    if (!element || !element.tagName) return String(element);
    const id = element.id ? "#" + element.id : "";
    const classes = typeof element.className === "string" && element.className.trim()
      ? "." + element.className.trim().split(/\s+/).slice(0, 3).join(".") : "";
    const text = (element.innerText || element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60);
    return element.tagName.toLowerCase() + id + classes + (text ? ' "' + text + '"' : "");
  }

  function add(rule, element, detail) {
    violations.push({ rule, node: describe(element), detail: detail || "" });
  }

  function hiddenFromEveryone(element) {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || node.hasAttribute("inert")) return true;
      if (node.tagName === "DETAILS" && !node.open && node !== element
          && !(element.tagName === "SUMMARY" && element.parentElement === node)) {
        const summary = node.querySelector(":scope > summary");
        if (!summary || !summary.contains(element)) return true;
      }
    }
    return element.getClientRects().length === 0;
  }

  function hiddenFromAssistiveTech(element) {
    return Boolean(element.closest("[aria-hidden=true]"));
  }

  function visuallyHidden(element) {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      if (rect.width <= 1 && rect.height <= 1 && style.overflow === "hidden") return true;
      if (style.clipPath === "inset(50%)" || style.clip === "rect(0px, 0px, 0px, 0px)") return true;
    }
    return false;
  }

  // ---- colour ----
  function parseColor(value) {
    if (!value || value === "transparent") return [0, 0, 0, 0];
    let match = value.match(/^rgba?\(([^)]+)\)$/);
    if (match) {
      const parts = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    }
    match = value.match(/^color\(srgb ([^)]+)\)$/);
    if (match) {
      const parts = match[1].split(/[\s/]+/).filter(Boolean).map(Number);
      return [parts[0] * 255, parts[1] * 255, parts[2] * 255, parts.length > 3 ? parts[3] : 1];
    }
    return null;
  }

  function over(top, bottom) {
    const alpha = top[3] + bottom[3] * (1 - top[3]);
    if (alpha === 0) return [0, 0, 0, 0];
    const channel = (index) => (top[index] * top[3] + bottom[index] * bottom[3] * (1 - top[3])) / alpha;
    return [channel(0), channel(1), channel(2), alpha];
  }

  function luminance(color) {
    const linear = color.slice(0, 3).map((value) => {
      const scaled = value / 255;
      return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  }

  function ratio(first, second) {
    const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
    return (light + 0.05) / (dark + 0.05);
  }

  // The opaque colour behind an element, composited from its ancestors; null when an image,
  // gradient or unparseable colour makes the backdrop undecidable from styles alone.
  function backdrop(element) {
    const layers = [];
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== "none") return null;
      const color = parseColor(style.backgroundColor);
      if (!color) return null;
      if (color[3] > 0) layers.push(color);
      if (color[3] >= 1) break;
    }
    let result = parseColor(getComputedStyle(document.body).backgroundColor) || [255, 255, 255, 1];
    if (result[3] < 1) result = over(result, [255, 255, 255, 1]);
    for (let index = layers.length - 1; index >= 0; index -= 1) result = over(layers[index], result);
    return result;
  }

  function opacity(element) {
    let value = 1;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      value *= Number(getComputedStyle(node).opacity);
    }
    return value;
  }

  function disabled(element) {
    return Boolean(element.closest("[disabled], [aria-disabled=true], [data-disabled]"));
  }

  // WCAG's minimums are hard: 4.499:1 fails 4.5:1, so the ratio is never rounded up.
  function measureText(element, paint, style, what) {
    const foreground = parseColor(paint);
    const behind = backdrop(element);
    if (!foreground || !behind) {
      add("color-contrast-undecided", element, what + " " + paint + " over an undecidable backdrop");
      return false;
    }
    const shown = over([foreground[0], foreground[1], foreground[2], foreground[3] * opacity(element)], behind);
    const size = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    const large = size >= LARGE_TEXT_PX || (bold && size >= LARGE_BOLD_TEXT_PX);
    const needed = large ? 3 : 4.5;
    const measured = ratio(shown, behind);
    if (measured < needed) {
      add("color-contrast", element, what + " " + measured.toFixed(2) + ":1 < " + needed + ":1 (" + paint + " on rgb(" + behind.slice(0, 3).map(Math.round).join(", ") + "))");
    }
    return true;
  }

  function checkTextContrast() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement;
      if (!element || seen.has(element) || !node.textContent.trim()) continue;
      seen.add(element);
      if (["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "OPTION"].includes(element.tagName)) continue;
      if (hiddenFromEveryone(element) || visuallyHidden(element) || disabled(element)) continue;
      const style = getComputedStyle(element);
      // SVG text paints with `fill`, not `color`; an unset fill is SVG's default black.
      const svg = element instanceof SVGElement;
      if (svg && style.fill === "none") continue;
      if (measureText(element, svg ? style.fill : style.color, style, svg ? "fill" : "text")) checked.text += 1;
    }
  }

  // A field's typed value, and its placeholder while empty, are text too.
  function checkFieldText() {
    const fields = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file]):not([type=submit]):not([type=button]):not([type=reset]), textarea, select";
    for (const field of document.querySelectorAll(fields)) {
      if (hiddenFromEveryone(field) || visuallyHidden(field) || disabled(field)) continue;
      const style = getComputedStyle(field);
      if (field.tagName === "SELECT" || field.value) {
        if (field.tagName === "SELECT" ? field.selectedOptions.length && field.selectedOptions[0].text.trim() : true) {
          if (measureText(field, style.color, style, "value")) checked.fields += 1;
        }
      } else if (field.placeholder) {
        const placeholder = getComputedStyle(field, "::placeholder");
        if (measureText(field, placeholder.color, placeholder, "placeholder")) checked.fields += 1;
      }
    }
  }

  // 1.4.11: an input's boundary must reach 3:1 against what surrounds it, unless its fill does.
  function checkControlBoundaries() {
    for (const input of document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea")) {
      if (hiddenFromEveryone(input) || disabled(input)) continue;
      // A pills or combobox field draws its boundary on the wrapping Mantine input.
      const control = input.classList.contains("mantine-Input-input") ? input
        : input.closest(".mantine-Input-input") || input;
      const style = getComputedStyle(control);
      const outside = backdrop(control.parentElement);
      const border = parseColor(style.borderTopColor);
      const fill = backdrop(control);
      if (!outside || !border || !fill) {
        add("non-text-contrast-undecided", input, "boundary " + style.borderTopColor + " over an undecidable backdrop");
        continue;
      }
      const borderWidth = parseFloat(style.borderTopWidth);
      const boundary = borderWidth > 0 ? ratio(over(border, outside), outside) : 0;
      if (boundary < 3 && ratio(fill, outside) < 3) {
        add("non-text-contrast", input, "control boundary " + boundary.toFixed(2) + ":1 < 3:1");
      }
    }
  }

  // ---- names ----
  function textOf(element) {
    let text = "";
    for (const child of element.childNodes) {
      if (child.nodeType === 3) text += child.textContent;
      else if (child.nodeType === 1) {
        if (child.getAttribute("aria-hidden") === "true") continue;
        if (getComputedStyle(child).display === "none") continue;
        if (child.tagName === "IMG") text += child.getAttribute("alt") || "";
        else if (child.hasAttribute("aria-label")) text += " " + child.getAttribute("aria-label") + " ";
        else text += " " + textOf(child) + " ";
      }
    }
    return text.replace(/\s+/g, " ").trim();
  }

  function accessibleName(element) {
    const labelledby = element.getAttribute("aria-labelledby");
    if (labelledby) {
      const text = labelledby.split(/\s+/).map((id) => document.getElementById(id))
        .filter(Boolean).map((node) => textOf(node)).join(" ").trim();
      if (text) return text;
    }
    const label = (element.getAttribute("aria-label") || "").trim();
    if (label) return label;
    if (element.labels && element.labels.length) {
      const text = [...element.labels].map((node) => textOf(node)).join(" ").trim();
      if (text) return text;
    }
    if (["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)) {
      if (["submit", "button", "reset"].includes(element.type) && element.value) return element.value;
      return (element.getAttribute("title") || element.getAttribute("placeholder") || "").trim();
    }
    return textOf(element) || (element.getAttribute("title") || "").trim();
  }

  function visibleText(element) {
    return (element.innerText || "").replace(/\s+/g, " ").trim();
  }

  function normalise(text) {
    return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  }

  function checkNames() {
    for (const element of document.querySelectorAll(INTERACTIVE)) {
      if (hiddenFromEveryone(element) && !visuallyHidden(element)) continue;
      if (hiddenFromAssistiveTech(element)) {
        if (element.tabIndex >= 0 && !disabled(element)) add("aria-hidden-focus", element, "focusable inside aria-hidden");
        continue;
      }
      checked.controls += 1;
      const name = accessibleName(element);
      if (!name) {
        add("control-name", element, "no accessible name");
        continue;
      }
      // 2.5.3 binds controls named from their content; a field's value or a region's body is not its label.
      const fromContent = element.matches(NAME_FROM_CONTENT);
      const shown = fromContent ? normalise(visibleText(element)) : "";
      const named = element.getAttribute("aria-label") || element.getAttribute("aria-labelledby");
      if (named && shown && /\p{L}/u.test(shown) && !(" " + normalise(name) + " ").includes(" " + shown + " ")) {
        add("label-in-name", element, 'name "' + name + '" does not contain visible text "' + shown + '"');
      }
      // 2.5.3 binds a field's visible <label> as well: an aria-label must keep the label's words.
      if (named && element.labels && element.labels.length) {
        const label = normalise([...element.labels].filter((node) => !hiddenFromEveryone(node) && !visuallyHidden(node))
          .map(visibleText).join(" "));
        if (label && /\p{L}/u.test(label) && !(" " + normalise(name) + " ").includes(" " + label + " ")) {
          add("label-in-name", element, 'name "' + name + '" does not contain visible label "' + label + '"');
        }
      }
      if (element.tabIndex > 0) add("tabindex", element, "positive tabindex " + element.tabIndex);
      if (element.matches("a[href], button") && element.querySelector(INTERACTIVE)) {
        add("nested-interactive", element, "contains another control");
      }
    }
    for (const image of document.querySelectorAll("img")) {
      if (!image.hasAttribute("alt") && !hiddenFromAssistiveTech(image)) add("image-alt", image, "img without alt");
    }
    for (const graphic of document.querySelectorAll("svg[role=img], [role=img]")) {
      if (!hiddenFromAssistiveTech(graphic) && !accessibleName(graphic)) add("image-alt", graphic, "role=img without a name");
    }
    for (const canvas of document.querySelectorAll("canvas")) {
      if (hiddenFromEveryone(canvas) || hiddenFromAssistiveTech(canvas)) continue;
      if (!canvas.getAttribute("aria-label") && !canvas.getAttribute("aria-labelledby")) add("image-alt", canvas, "chart canvas without a name");
    }
  }

  // ---- 2.5.8 target size ----
  function inlineInText(element) {
    if (getComputedStyle(element).display !== "inline") return false;
    const block = element.parentElement;
    if (!block) return false;
    const own = (element.textContent || "").trim().length;
    return (block.textContent || "").trim().length > own;
  }

  function checkTargets() {
    const targets = [];
    for (const element of document.querySelectorAll(INTERACTIVE)) {
      if (hiddenFromEveryone(element) || visuallyHidden(element)) continue;
      if (element.matches("[tabindex]:not(a, button, input, select, textarea, summary, [role])")) continue;
      if (element.matches("[role=option]")) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      targets.push({ element, rect });
    }
    checked.targets = targets.length;
    for (const { element, rect } of targets) {
      if (rect.width >= TARGET_PX && rect.height >= TARGET_PX) continue;
      if (element.tagName === "A" && inlineInText(element)) continue;
      if (element.matches("input[type=checkbox], input[type=radio]") && element.labels && element.labels.length) {
        const label = element.labels[0].getBoundingClientRect();
        if (label.height >= TARGET_PX || label.width >= TARGET_PX) continue;
      }
      // Spacing exception: a 24 px circle on the target's centre meets no other target's circle.
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const crowded = targets.some((other) => {
        if (other.element === element || other.element.contains(element) || element.contains(other.element)) return false;
        const ox = other.rect.left + other.rect.width / 2;
        const oy = other.rect.top + other.rect.height / 2;
        const dx = Math.max(other.rect.left - x, 0, x - other.rect.right);
        const dy = Math.max(other.rect.top - y, 0, y - other.rect.bottom);
        return Math.hypot(dx, dy) < TARGET_PX / 2 || Math.hypot(ox - x, oy - y) < TARGET_PX;
      });
      if (crowded) add("target-size", element, Math.round(rect.width) + "x" + Math.round(rect.height) + " px with a neighbour inside 24 px");
    }
  }

  // ---- structure ----
  function checkStructure() {
    if (!document.documentElement.lang) add("html-lang", document.documentElement, "missing lang");
    if (!document.title.trim()) add("document-title", document.documentElement, "empty title");
    const mains = [...document.querySelectorAll("main, [role=main]")];
    if (mains.length !== 1) add("landmark-main", document.body, mains.length + " main landmarks");
    const banners = [...document.querySelectorAll("header, [role=banner]")].filter((node) => !node.closest("main, article, aside, nav, section"));
    if (banners.length > 1) add("landmark-banner", document.body, banners.length + " banner landmarks");
    const navs = [...document.querySelectorAll("nav, [role=navigation]")];
    const navNames = navs.map((node) => accessibleName(node) || "");
    if (navs.length > 1 && new Set(navNames).size !== navs.length) add("landmark-unique", document.body, "navigation landmarks share a name");

    const headings = [...document.querySelectorAll("h1, h2, h3, h4, h5, h6, [role=heading]")]
      .filter((node) => !hiddenFromEveryone(node) && !hiddenFromAssistiveTech(node));
    const h1s = headings.filter((node) => node.tagName === "H1" || node.getAttribute("aria-level") === "1");
    if (h1s.length !== 1) add("page-has-one-h1", document.body, h1s.length + " level-one headings");
    let previous = 0;
    for (const heading of headings) {
      const level = heading.tagName.startsWith("H") ? Number(heading.tagName[1]) : Number(heading.getAttribute("aria-level") || 2);
      if (!textOf(heading)) add("empty-heading", heading, "heading has no text");
      if (previous && level > previous + 1) add("heading-order", heading, "h" + previous + " then h" + level);
      previous = level;
    }

    // Every visible piece of text sits in a landmark (the skip link and fixed toasts excepted).
    const landmarks = "section[aria-label], section[aria-labelledby], main, header, footer, nav, aside, [role=main], [role=banner], [role=contentinfo], [role=navigation], [role=region][aria-label], [role=region][aria-labelledby], [role=complementary], [role=dialog], [role=alertdialog], [aria-live], [role=status], [role=alert], [role=log]";
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const outside = new Set();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement;
      if (!element || !node.textContent.trim() || outside.has(element)) continue;
      if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(element.tagName)) continue;
      if (element.closest(".skip-link") || element.closest(landmarks)) continue;
      if (hiddenFromEveryone(element) || hiddenFromAssistiveTech(element)) continue;
      outside.add(element);
      add("region", element, "content outside every landmark");
    }

    const ids = new Map();
    for (const node of document.querySelectorAll("[id]")) ids.set(node.id, (ids.get(node.id) || 0) + 1);
    for (const attribute of ["aria-labelledby", "aria-describedby", "aria-controls", "aria-activedescendant", "for"]) {
      for (const node of document.querySelectorAll("[" + attribute + "]")) {
        if (hiddenFromEveryone(node) && !visuallyHidden(node)) continue;
        for (const id of node.getAttribute(attribute).split(/\s+/).filter(Boolean)) {
          if (!ids.has(id) && attribute !== "aria-controls") add("aria-valid-reference", node, attribute + " names missing #" + id);
          if (ids.get(id) > 1) add("duplicate-id-aria", node, attribute + " names duplicated #" + id);
        }
      }
    }
    for (const list of document.querySelectorAll("ul, ol")) {
      if (list.getAttribute("role")) continue;
      for (const child of list.children) {
        if (!["LI", "SCRIPT", "TEMPLATE"].includes(child.tagName)) add("list", list, "list holds a " + child.tagName.toLowerCase());
      }
    }
    // A region that scrolls must be reachable by keyboard and say what it holds.
    for (const element of document.body.querySelectorAll("*")) {
      const style = getComputedStyle(element);
      const scrolls = (["auto", "scroll"].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 1)
        || (["auto", "scroll"].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1);
      if (!scrolls || element === document.documentElement || hiddenFromEveryone(element)) continue;
      const reachable = element.tabIndex >= 0 && element.hasAttribute("tabindex");
      if (!reachable && !element.querySelector(INTERACTIVE)) add("scrollable-region-focusable", element, "scrolls but no Tab stop reaches it");
      else if (reachable && !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby")) add("scrollable-region-name", element, "focusable scroll region without a name");
    }
    for (const progress of document.querySelectorAll("progress, [role=progressbar]")) {
      if (!hiddenFromEveryone(progress) && !accessibleName(progress)) add("progressbar-name", progress, "progress without a name");
    }
  }

  // ---- 1.4.10 reflow ----
  function scrollsHorizontally(node) {
    const style = getComputedStyle(node);
    return ["auto", "scroll", "hidden", "clip"].includes(style.overflowX);
  }

  function checkReflow() {
    const width = document.documentElement.clientWidth;
    const overflow = document.documentElement.scrollWidth - width;
    if (overflow <= 0) return;
    const culprits = [];
    for (const element of document.body.querySelectorAll("*")) {
      if (hiddenFromEveryone(element) || getComputedStyle(element).position === "fixed") continue;
      const rect = element.getBoundingClientRect();
      if (rect.right <= width + 0.5) continue;
      // A scroller clips an absolutely positioned box only when it holds that box's containing block.
      const absolute = getComputedStyle(element).position === "absolute";
      const block = absolute ? element.offsetParent : null;
      let contained = false;
      for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
        if (scrollsHorizontally(node) && (!absolute || (block && (node === block || node.contains(block))))) {
          contained = true;
          break;
        }
      }
      if (contained) continue;
      if ([...element.children].some((child) => child.getBoundingClientRect().right > width + 0.5)) continue;
      culprits.push(element);
    }
    add("reflow", document.documentElement, "page scrolls " + overflow + " px sideways at " + width + " px; widest: "
      + culprits.slice(0, 5).map(describe).join(" | "));
  }

  checkReflow();
  checkStructure();
  checkNames();
  checkTargets();
  checkTextContrast();
  checkFieldText();
  checkControlBoundaries();
  return JSON.stringify({ violations, checked, width: document.documentElement.clientWidth });
})()
