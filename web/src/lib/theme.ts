/**
 * Theme and text size, applied to the document root.
 *
 * The theme is applied before the first render so the window does not flash the
 * wrong colors while the bundle loads.
 */
export type AppTheme = "light" | "dark" | "system";

const STORAGE_KEY = "noteagent.theme";
const FONT_SIZE_KEY = "noteagent.fontSize";
const BASE_FONT_SIZE_PX = 16;

let activeTheme: AppTheme = "system";
let mediaQuery: MediaQueryList | null = null;
let listenerInstalled = false;

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(theme: AppTheme): void {
  activeTheme = theme;
  const root = document.documentElement;
  const resolved =
    theme === "system" ? (systemPrefersDark() ? "dark" : "light") : theme;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
  root.dataset.theme = theme;
  root.style.colorScheme = resolved;
}

export function watchSystemTheme(): void {
  if (listenerInstalled) return;
  mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  mediaQuery.addEventListener("change", () => {
    if (activeTheme === "system") applyTheme("system");
  });
  listenerInstalled = true;
}

export function saveTheme(theme: AppTheme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* Storage is unavailable in private mode; the theme still applies in memory. */
  }
}

export function loadAndApplyTheme(): AppTheme {
  let theme: AppTheme = "system";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") {
      theme = saved;
    }
  } catch {
    /* ignore */
  }
  applyTheme(theme);
  return theme;
}

export function setTheme(theme: AppTheme): void {
  applyTheme(theme);
  saveTheme(theme);
}

/** Interface zoom, applied as the root font size so rem-based sizes follow. */
function applyFontSize(px: number): void {
  const clamped = Math.min(20, Math.max(12, px));
  document.documentElement.style.fontSize = `${clamped}px`;
}

export function loadAndApplyFontSize(): number {
  let px = BASE_FONT_SIZE_PX;
  try {
    const saved = Number(window.localStorage.getItem(FONT_SIZE_KEY));
    if (Number.isFinite(saved) && saved >= 12 && saved <= 20) px = saved;
  } catch {
    /* ignore */
  }
  applyFontSize(px);
  return px;
}

export function saveFontSize(px: number): void {
  try {
    window.localStorage.setItem(FONT_SIZE_KEY, String(px));
  } catch {
    /* ignore */
  }
}
