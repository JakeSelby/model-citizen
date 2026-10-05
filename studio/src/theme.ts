import {
  type CSSVariablesResolver,
  createTheme,
  defaultVariantColorsResolver,
  type MantineColorsTuple,
  type VariantColorsResolver,
} from "@mantine/core";

const clear: MantineColorsTuple = [
  "#E8F4F1",
  "#E8F4F1",
  "#E8F4F1",
  "#7CDDD0",
  "#7CDDD0",
  "#14635E",
  "#14635E",
  "#14635E",
  "#14635E",
  "#14635E",
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

export const studioTheme = createTheme({
  autoContrast: true,
  colors: { clear },
  primaryColor: "clear",
  primaryShade: { light: 6, dark: 3 },
  variantColorResolver: clearVariantColors,
  fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Consolas, monospace",
  defaultRadius: "md",
  components: {
    // Mantine stacks two steppers in one 44 px field, under the 24 px target floor (WCAG 2.5.8).
    // The arrow keys still step the value, and typing is the primary input.
    NumberInput: { defaultProps: { hideControls: true } },
  },
  headings: {
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    fontWeight: "650",
  },
});
