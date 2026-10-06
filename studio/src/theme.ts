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

export const studioCssVariables: CSSVariablesResolver = () => ({
  variables: {},
  light: paletteVariables(),
  dark: paletteVariables(),
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
      h1: { fontSize: "20px", lineHeight: "1.3" },
      h2: { fontSize: "14px", lineHeight: "1.4" },
      h3: { fontSize: "13px", lineHeight: "1.4" },
      h4: { fontSize: "13px", lineHeight: "1.4" },
    },
  },
});
