import type { GuestBounds, GuestTab } from "@/lib/desktop";

/**
 * Working out where the shell's browser should sit, and what it should show.
 *
 * The shell draws the page as its own surface over the window, so it is told an
 * area in the window's own coordinates rather than being handed a DOM node. The
 * page is laid out by the same window and never scrolls, so an element's position
 * in the viewport is that position.
 */

/** Below this, the page would be a sliver; the shell is told to show nothing. */
const MIN_GUEST_SIDE = 32;

/** The area a page would occupy, as the shell understands it. */
type Rect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/**
 * Somewhere for the shell to put the page, from an element's place on screen.
 *
 * These numbers are the window's own coordinates, which are CSS pixels while the
 * window is at zoom 1 — a shell pins that, and the two units are the same at any
 * display scale because window coordinates are scale-independent. A zoom would
 * make them differ, and would have to be multiplied out here before reporting.
 */
export function boundsFor(rect: Rect | null | undefined): GuestBounds {
  if (!rect) return hiddenBounds();
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  // A pane being resized, a tab that has been switched away, a module still
  // mounting: any of these can measure as nothing, and showing a page there would
  // paint over whatever the reader was looking at.
  if (width < MIN_GUEST_SIDE || height < MIN_GUEST_SIDE) return hiddenBounds();
  return {
    x: Math.round(rect.left),
    y: Math.round(rect.top),
    width,
    height,
    visible: true,
  };
}

/** No area at all, which is how the shell is told to take the page away. */
export function hiddenBounds(): GuestBounds {
  return { x: 0, y: 0, width: 0, height: 0, visible: false };
}

export function sameBounds(left: GuestBounds, right: GuestBounds): boolean {
  return (
    left.x === right.x &&
    left.y === right.y &&
    left.width === right.width &&
    left.height === right.height &&
    left.visible === right.visible
  );
}

/**
 * The address a typed line means, or nothing when it is not a web address.
 *
 * Only the web is reachable from the guest browser, so anything else — including
 * a scheme that would run script — is refused rather than handed to the shell.
 * A line without a scheme is read as a web address, which is how people write
 * them: `example.com` and `localhost:5174` are both somewhere to go, while
 * `javascript:` and `mailto:` are not.
 */
export function webAddress(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  // `scheme://` is an explicit scheme, and only the web is allowed. Without the
  // two slashes a colon is usually a port: `localhost:5174`, `example.com:8080`.
  const explicit = /^([a-z][a-z0-9+.-]*):\/\//i.exec(trimmed);
  if (explicit) {
    const scheme = explicit[1].toLowerCase();
    if (scheme !== "http" && scheme !== "https") return null;
  } else {
    // A bare `word:` with no dots is a scheme rather than a host — unless what
    // follows is a port. `mailto:a@b.com` and `javascript:alert(1)` are refused
    // this way, while `localhost:5174` is let through.
    const bare = /^([a-z][a-z0-9+.-]*):(?!\d)/i.exec(trimmed);
    if (bare) {
      const scheme = bare[1].toLowerCase();
      const hostlike = scheme.includes(".") || scheme === "http" || scheme === "https";
      if (!hostlike) return null;
    }
  }

  const candidate = explicit ? trimmed : `https://${trimmed}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  // A host is what makes it an address; a scheme alone is not somewhere to go.
  if (!url.hostname) return null;
  return url.toString();
}

/** What a tab is called in a list: its title, its address, or that it is new. */
export function tabLabel(tab: GuestTab, untitled: string): string {
  const title = tab.title.trim();
  // The shell sends the address as a tab's title while a page has none, which
  // reads as a duplicate of the line below it.
  if (title && title !== tab.url) return title;
  if (tab.url) return hostOf(tab.url);
  return untitled;
}

/**
 * Whether two addresses are the same page.
 *
 * A page can be recorded under the address it was requested by and shown under
 * the one it settled on, so the comparison ignores the trailing slash and the
 * fragment — neither changes which page it is — and keeps the query, which does.
 */
export function sameAddress(left: string, right: string): boolean {
  const normalize = (value: string): string | null => {
    try {
      const url = new URL(value.trim());
      const path = url.pathname.replace(/\/+$/, "");
      return `${url.protocol}//${url.host}${path}${url.search}`;
    } catch {
      return null;
    }
  };
  const a = normalize(left);
  const b = normalize(right);
  if (a === null || b === null) return left.trim() === right.trim();
  return a === b;
}

/** The site a page belongs to, for showing where a tab leads. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/**
 * Where in a site a tab is, for the line under its name.
 *
 * Nothing for a site's front page: repeating the site under a name that is already
 * the site says nothing.
 */
export function pathOf(url: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${path}${parsed.search}`;
  } catch {
    return "";
  }
}

/** The tab in front, or nothing when there are none yet. */
export function activeTab(tabs: GuestTab[] | undefined, activeId: number): GuestTab | null {
  if (!tabs || tabs.length === 0) return null;
  return tabs.find((tab) => tab.id === activeId) ?? null;
}

/**
 * The tab already showing an address, or nothing when the browser is elsewhere.
 *
 * Which page a tab is on decides both whether the browser can be asked for it and
 * whether offering to open it there would be offering what the reader already has,
 * so the comparison lives here rather than being made again wherever either is
 * asked. An empty address matches nothing: a tab that has yet to load one is not
 * the page being looked for.
 */
export function tabFor(tabs: GuestTab[] | undefined, url: string): GuestTab | null {
  const wanted = url.trim();
  if (!tabs || !wanted) return null;
  return tabs.find((tab) => sameAddress(tab.url, wanted)) ?? null;
}

/**
 * Opens an address in the browser the reader is already in.
 *
 * Without the shell there is no browser to host a page, but there is still a
 * browser — the one showing this app. Handing the address to it is what the module
 * can honestly offer there, and it is better than saying a page cannot be opened.
 */
export function openInThisBrowser(url: string): void {
  if (typeof window === "undefined") return;
  window.open(url, "_blank", "noreferrer");
}
