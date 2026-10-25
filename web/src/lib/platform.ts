/**
 * Platform helpers. Keyboard hints must not hardcode "Ctrl" — on macOS the
 * modifier is Command, and showing the wrong key is a common annoyance.
 */

function isMac(): boolean {
  if (typeof navigator === "undefined") return false;
  // userAgent covers modern Safari/Chrome on Apple hardware; iPads report as Mac.
  return /mac|iphone|ipad|ipod/i.test(navigator.userAgent);
}

/** "Ctrl+B" / "⌘B" / "Ctrl+Shift+2", for hints such as `Show the sidebar (Ctrl+B)`. */
export function shortcut(...keys: (string | number | false | undefined)[]): string {
  const parts = keys.filter((key): key is string | number => Boolean(key));
  if (parts.length === 0) return "";
  if (isMac() && parts.length === 1) return `⌘${parts[0]}`;
  return [isMac() ? "⌘" : "Ctrl", ...parts].join("+");
}
