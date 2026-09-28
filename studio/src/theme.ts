import {
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

const clearVariantColors: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  if (input.variant === "filled" && input.color === "clear") {
    return { ...colors, background: "var(--studio-primary)", hover: "var(--studio-primary)", color: "var(--studio-primary-foreground)" };
  }
  if (input.color === "clear" && ["light", "subtle", "outline"].includes(input.variant)) {
    return { ...colors, color: "var(--studio-primary)", background: input.variant === "light" ? "var(--studio-primary-wash)" : "transparent", hover: "var(--studio-primary-wash)" };
  }
  return colors;
};

export const studioTheme = createTheme({
  autoContrast: true,
  colors: { clear },
  primaryColor: "clear",
  primaryShade: { light: 6, dark: 3 },
  variantColorResolver: clearVariantColors,
  fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Consolas, monospace",
  defaultRadius: "md",
  headings: {
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    fontWeight: "650",
  },
});
