import type { ReactElement } from "react";

// One 24-unit stroke glyph per nav page, drawn in the link's own colour.
const GLYPHS: Record<string, ReactElement> = {
  home: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  sessions: <><path d="M4 19V5" /><path d="M4 19h16" /><path d="m7 15 4-4 3 3 5-6" /></>,
  rules: <><path d="M6 3h9l4 4v14H6z" /><path d="M9 12h7M9 16h5" /></>,
  stances: <><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></>,
  skills: <path d="m12 3 2.6 5.5 6 .8-4.4 4.1 1.1 5.9L12 16.4 6.7 19.3l1.1-5.9L3.4 9.3l6-.8z" />,
  hooks: <path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z" />,
  roles: <><circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0" /><circle cx="17" cy="9" r="2.5" /><path d="M16 14.2a5 5 0 0 1 5 5.8" /></>,
  workflows: <><circle cx="6" cy="6" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="12" r="2" /><path d="M8 6h3a3 3 0 0 1 3 3 3 3 0 0 0 2 2.8M8 18h3a3 3 0 0 0 3-3 3 3 0 0 1 2-2.8" /></>,
  sources: <><path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z" /><path d="M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  routines: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  changes: <><path d="M3 13h5l1.5 3h5l1.5-3h5" /><path d="M5.5 5h13L21 13v6H3v-6z" /></>,
  evals: <path d="M9 3h6M10 3v6l-5.5 10a1.3 1.3 0 0 0 1.1 2h12.8a1.3 1.3 0 0 0 1.1-2L14 9V3" />,
  baseline: <path d="M3 12h4l3-8 4 16 3-8h4" />,
};

export const NAV_ICON_KEYS = Object.keys(GLYPHS);

export function NavIcon({ name }: { name: string }) {
  const glyph = GLYPHS[name];
  if (!glyph) return null;
  return <svg aria-hidden="true" className="nav-icon" focusable="false" viewBox="0 0 24 24">{glyph}</svg>;
}
