import { useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
const KEY = "analyst_copilot_theme";

function stored(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch { /* unavailable: fall through */ }
  return "system";
}

/** "system" removes the attribute so the CSS media query decides. */
export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, set] = useState<Theme>(stored);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  return [
    theme,
    (next: Theme) => {
      set(next);
      try { localStorage.setItem(KEY, next); } catch { /* preference won't persist */ }
    },
  ];
}
