import {
  type CSSVariablesResolver,
  createTheme,
  defaultVariantColorsResolver,
  type MantineColorsTuple,
  type VariantColorsResolver,
} from "@mantine/core";

// The one accent: the operational study's muted teal (DESIGN.md, 2026-10-06). Shades 0-2 are its
// wash, 6-9 its foreground; the dark scheme reads shade 3.
const clear: MantineColorsTuple = [
  "#E6FCF5",
  "#E6FCF5",
  "#E6FCF5",
  "#63E6BE",
  "#63E6BE",
  "#087F5B",
  "#087F5B",
  "#087F5B",
  "#087F5B",
  "#087F5B",
];

// Every Mantine palette name the Studio uses resolves to a DESIGN.md semantic token, so a
// component given `color="red"` or `c="teal"` still draws from the tokens, whose foreground and
// wash pairs meet AA in both schemes. Filled surfaces swap the pair: the wash is the text.
type Tone = { color: string; wash: string };
const tone = (name: string): Tone => ({ color: `var(--studio-${name})`, wash: `var(--studio-${name}-wash)` });
const neutral: Tone = { color: "var(--studio-ink-secondary)", wash: "var(--studio-surface-canvas)" };
const TONES: Record<string, Tone> = {
  clear: tone("primary"), blue: tone("primary"), cyan: tone("primary"), indigo: tone("primary"),
  teal: tone("success"), green: tone("success"), lime: tone("success"),
  yellow: tone("warning"), orange: tone("warning"),
  red: tone("danger"), pink: tone("danger"),
  gray: neutral, dark: neutral,
};

export function toneFor(color: string | undefined): Tone | undefined {
  return color ? TONES[color.split(".")[0]] : undefined;
}

const clearVariantColors: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  if (input.variant === "filled" && input.color === "clear") {
    return { ...colors, background: "var(--studio-primary)", hover: "var(--studio-primary)", color: "var(--studio-primary-foreground)" };
  }
  const mapped = toneFor(input.color);
  if (!mapped) return colors;
  if (input.variant === "filled") {
    return { ...colors, background: mapped.color, hover: mapped.color, color: mapped.wash, border: "none" };
  }
  if (input.variant === "light") {
    return { ...colors, background: mapped.wash, hover: mapped.wash, color: mapped.color };
  }
  if (input.variant === "outline") {
    return { ...colors, background: "transparent", hover: mapped.wash, color: mapped.color, border: `1px solid ${mapped.color}` };
  }
  if (input.variant === "subtle") {
    return { ...colors, background: "transparent", hover: mapped.wash, color: mapped.color };
  }
  return colors;
};

function paletteVariables(): Record<string, string> {
  const variables: Record<string, string> = {
    "--mantine-color-error": "var(--studio-danger)",
    "--mantine-color-placeholder": "var(--studio-ink-secondary)",
  };
  for (const [name, mapped] of Object.entries(TONES)) {
    variables[`--mantine-color-${name}-text`] = mapped.color;
    variables[`--mantine-color-${name}-light`] = mapped.wash;
    variables[`--mantine-color-${name}-light-hover`] = mapped.wash;
    variables[`--mantine-color-${name}-light-color`] = mapped.color;
    variables[`--mantine-color-${name}-outline`] = mapped.color;
    variables[`--mantine-color-${name}-outline-hover`] = mapped.wash;
    variables[`--mantine-color-${name}-filled`] = mapped.color;
    variables[`--mantine-color-${name}-filled-hover`] = mapped.color;
  }
  return variables;
}

// The Studio's visual system, from the round-3 mocks' base.css: the one place a size, a radius or a
// colour is declared. Mantine's resolver emits them on :root, so every page and every page's CSS
// reads `var(--studio-*)` and none declares its own.
export const STUDIO_TOKENS = {
  shared: {
    "--studio-space-1": "4px",
    "--studio-space-2": "8px",
    "--studio-space-3": "12px",
    "--studio-space-4": "16px",
    "--studio-space-5": "20px",
    "--studio-space-6": "24px",
    "--studio-space-8": "32px",
    "--studio-space-10": "40px",
    "--studio-radius-sm": "4px",
    "--studio-radius-md": "6px",
    "--studio-radius-pill": "11px",
    "--studio-border-width": "1px",
    "--studio-focus-width": "2px",
    "--studio-focus-offset": "2px",
    // Compact controls stay above the 24 px target floor (WCAG 2.5.8).
    "--studio-control-height": "30px",
    "--studio-nav-item-height": "36px",
    "--studio-row-height": "30px",
    "--studio-icon-size": "16px",
    "--studio-text-xs": "12px",
    "--studio-text-sm": "13px",
    "--studio-text-md": "14px",
    "--studio-text-lg": "16px",
    "--studio-text-xl": "22px",
    "--studio-heading-size": "22px",
    "--studio-lead-size": "14px",
    "--studio-heading-tracking": "-0.01em",
    "--studio-label-tracking": "0",
    "--studio-header-height": "52px",
    "--studio-navbar-width": "220px",
    "--studio-min-width": "320px",
    "--studio-brand-size": "24px",
    "--studio-state-min-height": "0",
    "--studio-draft-width": "28rem",
    "--studio-overview-width": "60ch",
    "--studio-report-min-width": "360px",
    "--studio-theme-width": "9.5rem",
    "--studio-toast-width": "28rem",
    "--studio-toast-shadow": "0 2px 12px rgb(0 0 0 / 10%)",
    "--studio-skip-hidden": "-80px",
  },
  // Operational palette (DESIGN.md, 2026-10-06): a grey canvas under white panels, hairlines, one
  // muted teal accent. Status hues carry status only; each text shade keeps 4.5:1 on its canvas
  // and wash, and the brighter `-mark` shade draws the status dot.
  light: {
    "--studio-surface-canvas": "#F8F9FA",
    "--studio-surface-raised": "#FFFFFF",
    "--studio-surface-subtle": "#F8F9FA",
    "--studio-ink-primary": "#212529",
    "--studio-ink-secondary": "#646C73",
    "--studio-border-default": "#E9ECEF",
    "--studio-control-border": "#868E96",
    "--studio-primary": "#087F5B",
    "--studio-primary-foreground": "#FFFFFF",
    "--studio-primary-wash": "#E6FCF5",
    "--studio-success": "#237032",
    "--studio-success-mark": "#2B8A3E",
    "--studio-success-wash": "#EBFBEE",
    "--studio-warning": "#A85500",
    "--studio-warning-mark": "#E67700",
    "--studio-warning-wash": "#FFF4E6",
    "--studio-danger": "#C92A2A",
    "--studio-danger-mark": "#C92A2A",
    "--studio-danger-wash": "#FFF5F5",
    "--studio-neutral-mark": "#868E96",
  },
  dark: {
    "--studio-surface-canvas": "#16181B",
    "--studio-surface-raised": "#16181B",
    "--studio-surface-subtle": "#1F2226",
    "--studio-ink-primary": "#E9ECEF",
    "--studio-ink-secondary": "#A6A7AB",
    "--studio-border-default": "#2C2F34",
    "--studio-control-border": "#7C8189",
    "--studio-primary": "#63E6BE",
    "--studio-primary-foreground": "#0B2B22",
    "--studio-primary-wash": "#0F2A22",
    "--studio-success": "#8CE99A",
    "--studio-success-mark": "#51CF66",
    "--studio-success-wash": "#17301D",
    "--studio-warning": "#FFC078",
    "--studio-warning-mark": "#FF922B",
    "--studio-warning-wash": "#33260F",
    "--studio-danger": "#FF8787",
    "--studio-danger-mark": "#FF6B6B",
    "--studio-danger-wash": "#3A1A1A",
    "--studio-neutral-mark": "#7C8189",
  },
} as const;

export const studioCssVariables: CSSVariablesResolver = () => ({
  variables: { ...STUDIO_TOKENS.shared },
  light: { ...STUDIO_TOKENS.light, ...paletteVariables() },
  dark: { ...STUDIO_TOKENS.dark, ...paletteVariables() },
});

const SANS = "Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif";
const MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

export const studioTheme = createTheme({
  autoContrast: true,
  colors: { clear },
  primaryColor: "clear",
  primaryShade: { light: 6, dark: 3 },
  variantColorResolver: clearVariantColors,
  // Named first, never bundled: the Studio makes no network requests, so a machine without the
  // face falls back to the system stack.
  fontFamily: SANS,
  fontFamilyMonospace: MONO,
  defaultRadius: "sm",
  radius: { xs: "2px", sm: "4px", md: "6px", lg: "8px", xl: "12px" },
  fontSizes: { xs: "12px", sm: "13px", md: "14px", lg: "16px", xl: "18px" },
  lineHeights: { xs: "1.4", sm: "1.45", md: "1.5", lg: "1.5", xl: "1.4" },
  spacing: { xs: "6px", sm: "8px", md: "12px", lg: "16px", xl: "20px" },
  components: {
    // Mantine stacks two steppers in one 44 px field, under the 24 px target floor (WCAG 2.5.8).
    // The arrow keys still step the value, and typing is the primary input.
    NumberInput: { defaultProps: { hideControls: true } },
  },
  headings: {
    fontFamily: SANS,
    fontWeight: "600",
    sizes: {
      h1: { fontSize: "22px", lineHeight: "1.25" },
      h2: { fontSize: "14px", lineHeight: "1.4" },
      h3: { fontSize: "13px", lineHeight: "1.4" },
      h4: { fontSize: "13px", lineHeight: "1.4" },
    },
  },
});
