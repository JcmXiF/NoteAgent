/**
 * The desktop application's side of the world.
 *
 * The shell hosts a browser of its own (Electron's `webContentsView`) and lends it
 * to this interface; none of that is reachable from the page itself, so everything
 * here goes through the narrow object the shell exposes as `window.noteagentDesktop`.
 *
 * All of it is optional, and that is the point: the same interface runs in a plain
 * browser and in the desktop app. Every function here answers for both, so a view
 * never has to ask which one it is in — it asks whether the thing it wants is
 * available and says so when it is not.
 */

/** A page kept open in the shell's browser. */
export type GuestTab = {
  id: number;
  title: string;
  url: string;
  loading: boolean;
  active: boolean;
  /** The last response's status, when the shell has seen one. */
  httpStatus?: number | null;
  /** Whether the site has left any cookies, and how many. */
  hasCookies?: boolean;
  cookieCount?: number;
};

/** What the shell's browser has open, and where it is. */
export type GuestState = {
  tabs: GuestTab[];
  activeId: number;
  address: string;
  canGoBack: boolean;
  canGoForward: boolean;
  httpStatus?: number | null;
  hasCookies?: boolean;
  cookieCount?: number;
};

/**
 * Where the shell's browser should sit on screen, in the window's own coordinates.
 *
 * Sent as the window moves or resizes, because the browser is a separate surface
 * layered over the page: it does not follow a scroll or a layout change by itself.
 */
export type GuestBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
  visible: boolean;
};

/** A page taken from the shell's browser, as markup. */
type GuestCapture = {
  url: string;
  html: string;
};

export type NoteagentDesktop = {
  openGuest: (url: string) => Promise<void>;
  captureGuest: () => Promise<GuestCapture>;
  getGuestState?: () => Promise<GuestState>;
  newGuestTab?: (url?: string) => Promise<void>;
  closeGuestTab?: (id: number) => Promise<void>;
  switchGuestTab?: (id: number) => Promise<void>;
  navigateGuest?: (url: string) => Promise<void>;
  guestBack?: () => Promise<void>;
  guestForward?: () => Promise<void>;
  guestReload?: () => Promise<void>;
  setGuestBounds?: (bounds: GuestBounds) => Promise<void>;
  /**
   * The operating system's folder picker, answering with the chosen path.
   *
   * A page cannot read a directory out of a file input, so choosing one is the
   * shell's job. An empty string means nothing was chosen, which is not a failure.
   */
  pickFolder?: () => Promise<string>;
  /** The shell has drawn a frame; the bounds are worth recomputing. */
  onFrame?: (handler: () => void) => () => void;
  /** Something asked for the browser to be shown, e.g. a link in another app. */
  onShowBrowse?: (handler: () => void) => () => void;
  onGuestUrl: (handler: (url: string) => void) => () => void;
  onGuestState?: (handler: (state: GuestState) => void) => () => void;
  onCaptured: (handler: (payload: GuestCapture) => void) => () => void;
};

declare global {
  interface Window {
    noteagentDesktop?: NoteagentDesktop;
  }
}

/**
 * The shell's interface, or nothing when this is not the desktop app.
 *
 * A page can be opened in a browser, in a test, or served before the shell's
 * bridge is there; asking is always safe, and "not there" is the answer rather
 * than an error.
 */
export function desktopBridge(): NoteagentDesktop | undefined {
  if (typeof window === "undefined") return undefined;
  const bridge = window.noteagentDesktop;
  // A page can put anything on `window`; only the bridge's own shape counts.
  if (!bridge || typeof bridge !== "object") return undefined;
  return bridge;
}

/** Whether this is the desktop app, for saying what is and is not available. */
export function hasDesktop(): boolean {
  return desktopBridge() !== undefined;
}

/**
 * Whether a folder can be chosen from the operating system's picker.
 *
 * False in a plain browser, where the path field is the only way in; the dialog
 * asks before offering a button that would do nothing.
 */
export function canPickFolder(): boolean {
  return Boolean(desktopBridge()?.pickFolder);
}

/**
 * The shell's folder picker.
 *
 * Answers with the chosen path, or null — because nothing was chosen, or because
 * this is a plain browser, where there is no picker to ask. Either way the caller
 * keeps its path field usable: a folder can always be typed.
 */
export async function pickFolder(): Promise<string | null> {
  const bridge = desktopBridge();
  if (!bridge?.pickFolder) return null;
  try {
    const chosen = await bridge.pickFolder();
    return typeof chosen === "string" && chosen.trim() ? chosen : null;
  } catch {
    return null;
  }
}

/**
 * Whether a page refused to be captured because the reader is not signed in.
 *
 * A site answers a signed-out reader with these rather than with the page, and
 * saying so is more useful than reporting a failed capture.
 */
export function guestCaptureBlocked(status: number | null | undefined): boolean {
  return status === 401 || status === 403;
}
