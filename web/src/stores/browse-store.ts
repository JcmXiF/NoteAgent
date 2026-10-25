import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import { activeTab, openInThisBrowser, tabFor, webAddress } from "@/lib/browse";
import { promoteDocument } from "@/stores/docs-store";
import { rememberReading } from "@/stores/reading-store";
import { desktopBridge, guestCaptureBlocked, hasDesktop, type GuestState } from "@/lib/desktop";
import { useAcquireStore } from "@/stores/acquire-store";
import { useNavStore } from "@/stores/nav-store";
import type { PaneSlot } from "@/stores/layout-store";
import i18n from "@/i18n";

/**
 * The page in the built-in browser, and the tabs it is one of.
 *
 * The shell owns the browser — it has the tabs, the history and the cookies — and
 * reports what it has; this holds that report so a view can draw a tab list and an
 * address bar. Nothing here is the source of truth: whatever the shell says next
 * replaces it.
 *
 * There is one browser per window rather than one per pane, so this is one store
 * rather than state inside whichever view happens to be showing it.
 */

const EMPTY: GuestState = {
  tabs: [],
  activeId: 0,
  address: "",
  canGoBack: false,
  canGoForward: false,
};

type BrowseState = {
  /** Whether this window has the shell's browser at all. */
  available: boolean;
  /** Whether the shell has been heard from; before that, nothing is known. */
  connected: boolean;
  guest: GuestState;
  /** The page area the browser is being shown in, and the pane that owns it. */
  claimed: { slot: PaneSlot; element: HTMLElement } | null;
  /** What is in the address bar, which the reader may be editing. */
  address: string;
  /** Whether the reader is in the address bar, and so owns what is in it. */
  addressFocused: boolean;
  /** Why the last request was refused, said where the address bar is. */
  error: string | null;

  /**
   * The page area the browser is being shown in, and which pane owns it.
   *
   * One pane at a time, because the shell has one browser: claiming the area is how
   * a pane says "show it here". The measuring is done by whoever watches this, so
   * two panes cannot both decide where the page goes.
   */
  claim: (slot: PaneSlot, element: HTMLElement) => void;
  /** Gives the area up, when the pane stops showing the browser. */
  release: (slot: PaneSlot) => void;

  /**
   * Starts listening to the shell.
   *
   * Called by whichever view shows the browser. Repeated calls do nothing: the
   * listening belongs to the window, not to the view that asked for it.
   */
  attach: () => void;
  setAddress: (text: string) => void;
  setAddressFocused: (focused: boolean) => void;
  /** Goes to an address in the tab in front. */
  navigate: (text: string) => Promise<void>;
  /** Opens a page, returning to a tab that already has it. */
  open: (url: string) => Promise<void>;
  /**
   * Keeps the page in front, as it is rendered.
   *
   * The shell serializes the page — scripts already run, images inlined — and hands
   * it over; the core reads it as it would a fetched page. The markup comes through
   * `onCaptured` rather than through this call's answer, so a capture the shell
   * starts itself (a menu item, say) is kept the same way; this call is the request,
   * and its failure is what says a capture could not be taken.
   */
  keepPage: () => Promise<void>;
  /**
   * Takes a page from the browser when the browser is still on it.
   *
   * A page that only appears once signed in cannot be fetched from here: the
   * request arrives as a stranger, however the reader is signed in. The browser may
   * still be holding it, and asking it to serialize what it has is the same capture
   * the reader would get by hand.
   *
   * Answers whether the browser was on that page, so the caller knows whether to
   * fall back to a request.
   */
  captureIfOpen: (url: string) => Promise<boolean>;
  newTab: (url?: string) => Promise<void>;
  closeTab: (id: number) => Promise<void>;
  switchTab: (id: number) => Promise<void>;
  back: () => Promise<void>;
  forward: () => Promise<void>;
  reload: () => Promise<void>;
  reset: () => void;
};

/** The listening belongs to the window, so it is started once and never stopped. */
let attached = false;

export const useBrowseStore = create<BrowseState>((set, get) => {
  /** Takes the shell's report as the new truth. */
  function apply(next: GuestState): void {
    set((state) => ({
      guest: next,
      connected: true,
      // The address follows the page, unless the reader is writing in it: taking
      // a half-typed address away is worse than leaving a stale one.
      address: state.addressFocused ? state.address : next.address,
      error: null,
    }));
  }

  /** Runs one of the shell calls, saying so when it does not answer. */
  async function tell(call: () => Promise<unknown> | undefined): Promise<void> {
    try {
      await call();
    } catch (err) {
      set({ error: errorMessage(err, i18n.t("browse.actionFailed")) });
    }
  }

  return {
    available: hasDesktop(),
    connected: false,
    guest: EMPTY,
    address: "",
    addressFocused: false,
    error: null,
    claimed: null,

    attach: () => {
      const bridge = desktopBridge();
      if (!bridge || attached) return;
      attached = true;
      set({ available: true });

      bridge.onGuestState?.(apply);
      bridge.onGuestUrl?.((url) => set({ guest: { ...get().guest, address: url } }));
      bridge.onCaptured((payload) => {
        // One channel for the page the shell serialized, whoever asked for it.
        void useAcquireStore.getState().takeRendered(payload.url, payload.html);
      });
      void bridge.getGuestState?.().then((next) => {
        if (next) apply(next);
      });
      // A shell that cannot report its tabs can still take requests; there is
      // then no state to wait for.
      if (!bridge.getGuestState) set({ connected: true });
    },

    setAddress: (text) => set({ address: text, error: null }),
    setAddressFocused: (focused) => set({ addressFocused: focused }),

    claim: (slot, element) => set({ claimed: { slot, element } }),
    release: (slot) =>
      set((state) => (state.claimed?.slot === slot ? { claimed: null } : {})),

    navigate: async (text) => {
      const bridge = desktopBridge();
      const url = webAddress(text);
      if (!url) {
        // Saying which part was wrong beats passing it on and reporting the answer.
        set({ error: i18n.t("browse.notAnAddress") });
        return;
      }
      set({ error: null });
      if (!bridge?.navigateGuest) {
        // No shell, or one that cannot be told where to go: the page still has
        // somewhere to open, which is the browser the reader is in.
        openInThisBrowser(url);
        return;
      }
      await tell(() => bridge.navigateGuest?.(url));
    },

    open: async (url) => {
      const bridge = desktopBridge();
      const href = webAddress(url);
      if (!href) {
        set({ error: i18n.t("browse.notAnAddress") });
        return;
      }
      set({ error: null });
      if (!bridge) {
        openInThisBrowser(href);
        return;
      }
      // Letting the shell choose means an open page is returned to rather than
      // opened a second time.
      await tell(() => bridge.openGuest(href));
    },

    newTab: async (url) => {
      const bridge = desktopBridge();
      const href = url ? webAddress(url) : null;
      if (url && !href) {
        set({ error: i18n.t("browse.notAnAddress") });
        return;
      }
      // A shell that cannot open tabs is not a reason to complain: there is still a
      // browser, and the view does not offer the button in that case.
      if (!bridge?.newGuestTab) return;
      set({ error: null });
      await tell(() => bridge.newGuestTab?.(href ?? undefined));
    },

    closeTab: async (id) => {
      const bridge = desktopBridge();
      if (!bridge?.closeGuestTab) return;
      await tell(() => bridge.closeGuestTab?.(id));
    },

    keepPage: async () => {
      const bridge = desktopBridge();
      // Without the shell there is no page to serialize, and the view already says
      // as much; this is simply nothing to do.
      if (!bridge) return;
      const tab = activeTab(get().guest.tabs, get().guest.activeId);
      const status = tab?.httpStatus ?? null;
      if (guestCaptureBlocked(status)) {
        // Refused here rather than passed on: the shell would refuse it too, and
        // saying which page needs signing in is more use than the shell's wording.
        set({ error: i18n.t("browse.keep.blocked") });
        return;
      }
      set({ error: null });
      // The serialized page arrives through `onCaptured`, so this only has to say
      // whether a capture could be taken at all.
      await tell(() => bridge.captureGuest());
    },

    captureIfOpen: async (url) => {
      const bridge = desktopBridge();
      if (!bridge) return false;
      const { guest } = get();
      const tab = tabFor(guest.tabs, url);
      if (!tab) return false;
      // A capture is of the page in front, so the tab has to be the one in front.
      if (tab.id !== guest.activeId && bridge.switchGuestTab) {
        await tell(() => bridge.switchGuestTab?.(tab.id));
      }
      await get().keepPage();
      // An error here is the capture's own refusal (a sign-in wall, say), and it is
      // already said where the address bar is; the caller does not retry over HTTP.
      return true;
    },

    switchTab: async (id) => {
      const bridge = desktopBridge();
      if (!bridge?.switchGuestTab) return;
      await tell(() => bridge.switchGuestTab?.(id));
    },

    back: async () => {
      const bridge = desktopBridge();
      if (!bridge?.guestBack) return;
      await tell(() => bridge.guestBack?.());
    },

    forward: async () => {
      const bridge = desktopBridge();
      if (!bridge?.guestForward) return;
      await tell(() => bridge.guestForward?.());
    },

    reload: async () => {
      const bridge = desktopBridge();
      if (!bridge?.guestReload) return;
      await tell(() => bridge.guestReload?.());
    },

    reset: () =>
      set({
        available: hasDesktop(),
        connected: false,
        guest: EMPTY,
        address: "",
        addressFocused: false,
        error: null,
        claimed: null,
      }),
  };
});

/**
 * Opens a source's original page where it can actually be signed in to.
 *
 * A page behind a sign-in is only keepable from the shell's browser, because that
 * is the only place the site's own cookies live; opening it in the system browser
 * instead would send the reader somewhere the advice they were given does not
 * apply. So with a shell the original takes the pane the page being read is in,
 * because it is the same thing seen at its source: the page that was there can be
 * gone back to. The window keeps one browser, so the original cannot open beside
 * the page it belongs to — a second pane showing it would be a frame with nothing
 * in it. The reader asks from the page in question, so the pane they asked from is
 * the pane it belongs in.
 *
 * `from` is that pane, told by the view doing the asking: a source can be read in
 * either pane, and only the view knows which one it is drawn in.
 *
 * Without a shell the page opens as a tab, which is all there is.
 */
export function openOriginal(url: string, from: PaneSlot): void {
  const href = url.trim();
  if (!href) return;
  if (!hasDesktop()) {
    openInThisBrowser(href);
    return;
  }
  const plan = useNavStore.getState().go(
    { kind: "browse", url: href },
    { placement: "in-place", from },
  );
  // The note moves with the pane, and the shell reports its page only later, so
  // where the reader now is is written down rather than read back.
  if (plan.promoteFrom) promoteDocument(plan.promoteFrom);
  rememberReading({ slot: plan.target, module: "browse", target: href });
}

/**
 * Answers requests from other views to open a page here.
 *
 * Subscribed once for the window, the way the other stores do it: an intent is one
 * request, and a view that happened to be mounted twice would otherwise answer it
 * twice.
 */
let started = false;

export function startBrowseSync(): void {
  if (started) return;
  started = true;

  useNavStore.subscribe((state, previous) => {
    if (state.seq === previous.seq) return;
    const intent = state.intent;
    if (!intent || intent.kind !== "browse" || !intent.url) return;
    void useBrowseStore.getState().open(intent.url);
  });
}
