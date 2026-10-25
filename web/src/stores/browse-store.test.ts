import { describe, expect, it, vi } from "vitest";
import type { GuestState } from "@/lib/desktop";
import i18n from "@/i18n";

vi.mock("@/lib/desktop", () => ({
  desktopBridge: vi.fn(),
  hasDesktop: vi.fn(),
  guestCaptureBlocked: (status: number | null | undefined) =>
    status === 401 || status === 403,
}));

/** What keeping a page amounts to, kept apart from the workbench's own tests. */
const takeRendered = vi.fn(async () => {});
vi.mock("@/stores/acquire-store", () => ({
  useAcquireStore: { getState: () => ({ takeRendered }) },
}));

/** What the shell reports for a window with one page open. */
const STATE: GuestState = {
  tabs: [
    {
      id: 1,
      title: "Example",
      url: "https://example.com/",
      loading: false,
      active: true,
      httpStatus: 200,
    },
  ],
  activeId: 1,
  address: "https://example.com/",
  canGoBack: false,
  canGoForward: false,
};

/** The same, with the page in front answering the given status. */
function stateWithStatus(status: number): GuestState {
  return {
    ...STATE,
    httpStatus: status,
    tabs: STATE.tabs.map((tab) => ({ ...tab, httpStatus: status })),
  };
}

/**
 * The store remembers that it has attached, because the shell's browser belongs to
 * the window rather than to a view. Each test therefore starts from a fresh copy of
 * the module, as a reload would.
 */
async function freshStore(options: { host?: Record<string, unknown> } = {}) {
  vi.resetModules();
  const desktop = await import("@/lib/desktop");
  // Loosely typed on purpose: a shell is a bag of calls, and a test only cares
  // about the ones it overrides.
  const host = {
    openGuest: vi.fn(async () => {}),
    captureGuest: vi.fn(async () => ({ url: "", html: "" })),
    onGuestUrl: vi.fn(() => () => {}),
    onCaptured: vi.fn(() => () => {}),
    ...options.host,
  } as unknown as Record<string, ReturnType<typeof vi.fn>>;
  vi.mocked(desktop.desktopBridge).mockReturnValue(
    (options.host ? host : undefined) as never,
  );
  vi.mocked(desktop.hasDesktop).mockReturnValue(Boolean(options.host));
  const store = (await import("@/stores/browse-store")).useBrowseStore;
  const openOriginal = (await import("@/stores/browse-store")).openOriginal;
  return { store, openOriginal, host, desktop };
}

describe("without a shell", () => {
  it("hands the address to the browser the reader is already in", async () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    const { store } = await freshStore();
    expect(store.getState().available).toBe(false);

    await store.getState().open("example.com");
    // There is no shell to host a page, but there is still a browser.
    expect(open).toHaveBeenCalledWith("https://example.com/", "_blank", "noreferrer");
    expect(store.getState().error).toBeNull();
    vi.unstubAllGlobals();
  });

  it("opens a typed address the same way", async () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    const { store } = await freshStore();
    await store.getState().navigate("example.com/a");
    expect(open).toHaveBeenCalledWith("https://example.com/a", "_blank", "noreferrer");
    vi.unstubAllGlobals();
  });

  it("still refuses what is not a web address", async () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    const { store } = await freshStore();
    await store.getState().open("javascript:alert(1)");
    expect(open).not.toHaveBeenCalled();
    expect(store.getState().error).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("does not answer the controls that need a hosted page", async () => {
    const { store } = await freshStore();
    await expect(store.getState().back()).resolves.toBeUndefined();
    await expect(store.getState().newTab()).resolves.toBeUndefined();
    expect(store.getState().error).toBeNull();
    expect(store.getState().guest.tabs).toEqual([]);
  });
});

describe("with a shell", () => {
  it("takes the tabs the shell reports as the truth", async () => {
    const listeners: ((state: GuestState) => void)[] = [];
    const { store, host } = await freshStore({
      host: {
        onGuestState: (handler: (state: GuestState) => void) => {
          listeners.push(handler);
          return () => {};
        },
        getGuestState: async () => STATE,
      },
    });

    store.getState().attach();
    await Promise.resolve();
    await Promise.resolve();

    expect(store.getState().connected).toBe(true);
    expect(store.getState().guest.tabs).toHaveLength(1);
    expect(store.getState().address).toBe("https://example.com/");

    // A later report replaces it wholesale: the shell owns the browser.
    listeners[0]?.({ ...STATE, address: "https://example.com/two", canGoBack: true });
    expect(store.getState().guest.canGoBack).toBe(true);
    expect(store.getState().address).toBe("https://example.com/two");
    expect(host.getGuestState).toBeDefined();
  });

  it("leaves an address the reader is writing alone", async () => {
    const listeners: ((state: GuestState) => void)[] = [];
    const { store } = await freshStore({
      host: {
        onGuestState: (handler: (state: GuestState) => void) => {
          listeners.push(handler);
          return () => {};
        },
        getGuestState: async () => STATE,
      },
    });
    store.getState().attach();
    await Promise.resolve();
    await Promise.resolve();

    store.getState().setAddressFocused(true);
    store.getState().setAddress("half-typed");
    listeners[0]?.({ ...STATE, address: "https://example.com/elsewhere" });

    // Taking a half-written address away is worse than leaving a stale one.
    expect(store.getState().address).toBe("half-typed");
  });

  it("reads a typed address as one on the web", async () => {
    const { store, host } = await freshStore({ host: {} });
    await store.getState().open("example.com/a");
    expect(host.openGuest).toHaveBeenCalledWith("https://example.com/a");
    expect(store.getState().error).toBeNull();
  });

  it("refuses to hand anything but a web address to the shell", async () => {
    const { store, host } = await freshStore({ host: {} });
    await store.getState().open("javascript:alert(1)");
    expect(host.openGuest).not.toHaveBeenCalled();
    expect(store.getState().error).toBeTruthy();
  });

  it("says which part was wrong for a typed line, and sends nothing", async () => {
    const { store, host } = await freshStore({
      host: { navigateGuest: vi.fn(async () => {}) },
    });
    await store.getState().navigate("not a url");
    expect(host.navigateGuest).not.toHaveBeenCalled();
    // Said here, in words, rather than passed on to come back as an answer.
    expect(store.getState().error).toBeTruthy();

    await store.getState().navigate("example.com");
    expect(host.navigateGuest).toHaveBeenCalledWith("https://example.com/");
    expect(store.getState().error).toBeNull();
  });

  it("passes the history and tab actions through", async () => {
    const { store, host } = await freshStore({
      host: {
        guestBack: vi.fn(async () => {}),
        guestForward: vi.fn(async () => {}),
        guestReload: vi.fn(async () => {}),
        newGuestTab: vi.fn(async () => {}),
        closeGuestTab: vi.fn(async () => {}),
        switchGuestTab: vi.fn(async () => {}),
      },
    });

    await store.getState().back();
    await store.getState().forward();
    await store.getState().reload();
    await store.getState().newTab("example.com");
    await store.getState().closeTab(3);
    await store.getState().switchTab(4);

    expect(host.guestBack).toHaveBeenCalled();
    expect(host.guestForward).toHaveBeenCalled();
    expect(host.guestReload).toHaveBeenCalled();
    expect(host.newGuestTab).toHaveBeenCalledWith("https://example.com/");
    expect(host.closeGuestTab).toHaveBeenCalledWith(3);
    expect(host.switchGuestTab).toHaveBeenCalledWith(4);
    expect(store.getState().error).toBeNull();
  });

  it("says when the shell does not answer", async () => {
    const { store } = await freshStore({
      host: {
        openGuest: vi.fn(async () => {
          throw new Error("no such window");
        }),
      },
    });
    await store.getState().open("https://example.com/");
    // The shell's own wording is for a log ("no such window" says nothing about what
    // the reader was doing); the pane says something written for them.
    expect(store.getState().error).toBe(i18n.t("browse.actionFailed"));
  });

  it("offers only what a shell has grown", async () => {
    // A shell built before tabs existed still opens pages; the store does not
    // pretend the rest is there, and does not complain about it either.
    const { store } = await freshStore({
      host: {
        openGuest: vi.fn(async () => {}),
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        onGuestUrl: vi.fn(() => () => {}),
        onCaptured: vi.fn(() => () => {}),
      },
    });
    await expect(store.getState().closeTab(1)).resolves.toBeUndefined();
    await expect(store.getState().newTab()).resolves.toBeUndefined();
    await expect(store.getState().back()).resolves.toBeUndefined();
    expect(store.getState().error).toBeNull();
    // Opening a page still works, because that part is there.
    await store.getState().open("https://example.com/");
    expect(store.getState().error).toBeNull();
  });
});

/** Lets the shell's answer land before an action that depends on it. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("taking a page the browser still has", () => {
  const twoTabs: GuestState = {
    ...STATE,
    tabs: [
      { ...STATE.tabs[0]!, id: 1, url: "https://example.com/one" },
      { ...STATE.tabs[0]!, id: 2, url: "https://example.com/two", active: false },
    ],
    activeId: 1,
  };

  it("serializes it when the browser is on it", async () => {
    const { store, host } = await freshStore({
      host: {
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        getGuestState: async () => STATE,
      },
    });
    store.getState().attach();
    await settled();

    await expect(store.getState().captureIfOpen("https://example.com/")).resolves.toBe(true);
    expect(host.captureGuest).toHaveBeenCalled();
  });

  it("brings a tab in front before taking it", async () => {
    // A capture is of the page in front, so the tab has to be front first.
    const { store, host } = await freshStore({
      host: {
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        switchGuestTab: vi.fn(async () => {}),
        getGuestState: async () => twoTabs,
      },
    });
    store.getState().attach();
    await settled();

    await store.getState().captureIfOpen("https://example.com/two");
    expect(host.switchGuestTab).toHaveBeenCalledWith(2);
    expect(host.captureGuest).toHaveBeenCalled();
  });

  it("says no when the browser is somewhere else", async () => {
    const { store, host } = await freshStore({
      host: {
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        getGuestState: async () => STATE,
      },
    });
    store.getState().attach();
    await settled();

    // The caller falls back to a request, which is the only other way to get it.
    await expect(store.getState().captureIfOpen("https://example.com/other")).resolves.toBe(false);
    expect(host.captureGuest).not.toHaveBeenCalled();
  });

  it("says no when there is no browser to take it from", async () => {
    const { store } = await freshStore();
    await expect(store.getState().captureIfOpen("https://example.com/")).resolves.toBe(false);
  });
});

describe("opening a source's original page", () => {
  it("shows it in place of what is being read, with a way back", async () => {
    const { store, openOriginal } = await freshStore({ host: {} });
    const nav = (await import("@/stores/nav-store")).useNavStore;
    const layout = (await import("@/stores/layout-store")).useLayoutStore;
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    layout.setState({ view: "docs", side: null, focused: "main", paneReturn: {} });

    openOriginal("https://example.com/a", "main");

    // A page behind a sign-in can only be signed in to in the shell's browser, so
    // that is where the advice the reader was given applies.
    expect(open).not.toHaveBeenCalled();
    expect(nav.getState().intent).toEqual({ kind: "browse", url: "https://example.com/a" });
    // The page is the same thing seen at its source, so it takes the place of the
    // note rather than opening a second window — and the note can be returned to.
    expect(nav.getState().plan?.target).toBe("main");
    expect(layout.getState().view).toBe("browse");
    expect(layout.getState().side).toBeNull();
    expect(layout.getState().paneReturn.main).toBe("docs");
    expect(store.getState().available).toBe(true);
    vi.unstubAllGlobals();
  });

  it("keeps the browser where it is when it is already on screen", async () => {
    const { openOriginal } = await freshStore({ host: {} });
    const layout = (await import("@/stores/layout-store")).useLayoutStore;
    const nav = (await import("@/stores/nav-store")).useNavStore;
    // The reader is in the note and the browser has the other pane. The window keeps
    // one browser, so the original goes back to the pane the browser already owns
    // rather than being shown a second time.
    layout.setState({
      view: "docs",
      side: "browse",
      focused: "side",
      paneReturn: { side: "docs" },
    });

    openOriginal("https://example.com/a", "main");

    expect(nav.getState().plan?.target).toBe("side");
    expect(layout.getState().view).toBe("docs");
    expect(layout.getState().side).toBe("browse");
    // The shell can only put the page where the focus is.
    expect(layout.getState().focused).toBe("side");
    // The pane has not changed what it is, so what it can go back to still stands.
    expect(layout.getState().paneReturn.side).toBe("docs");
  });

  it("gives the original the pane the page being read is in, with a way back", async () => {
    const { openOriginal } = await freshStore({ host: {} });
    const layout = (await import("@/stores/layout-store")).useLayoutStore;
    const docs = (await import("@/stores/docs-store")).useDocsStore;
    layout.setState({ view: "docs", side: "docs", focused: "side", paneReturn: {} });
    docs.setState({ project: "p", paths: { main: "notes/a.md", side: "notes/b.md" } });

    // The view says which pane the reader is in; that is the one thing about the
    // request that cannot be read off the panes afterwards.
    openOriginal("https://example.com/a", "side");

    expect(layout.getState().side).toBe("browse");
    // The other pane is left alone.
    expect(layout.getState().view).toBe("docs");
    // The original took the place of the note the reader was reading, so that is
    // what the pane offers to go back to.
    expect(layout.getState().paneReturn.side).toBe("docs");
    // The note is still the pane's document: going back is a return, not a reopening.
    expect(docs.getState().paths.side).toBe("notes/b.md");
    expect(docs.getState().paths.main).toBe("notes/a.md");
  });

  it("opens it as a tab when there is no browser to sign in to", async () => {
    const { openOriginal } = await freshStore();
    const open = vi.fn();
    vi.stubGlobal("window", { open });

    openOriginal("https://example.com/a", "main");

    expect(open).toHaveBeenCalledWith("https://example.com/a", "_blank", "noreferrer");
    vi.unstubAllGlobals();
  });

  it("does nothing for an empty address", async () => {
    const { openOriginal } = await freshStore({ host: {} });
    const nav = (await import("@/stores/nav-store")).useNavStore;
    const before = nav.getState().seq;
    openOriginal("   ", "main");
    expect(nav.getState().seq).toBe(before);
  });
});

describe("keeping the page in front", () => {
  it("asks the shell to serialize it", async () => {
    const { store, host } = await freshStore({
      host: { captureGuest: vi.fn(async () => ({ url: "", html: "" })) },
    });
    store.getState().attach();
    await Promise.resolve();

    await store.getState().keepPage();

    expect(host.captureGuest).toHaveBeenCalled();
    expect(store.getState().error).toBeNull();
  });

  it("does not even ask while the page is behind a sign-in", async () => {
    // The shell would refuse too; saying which page needs signing in is more use
    // than passing on its wording.
    const { store, host } = await freshStore({
      host: {
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        getGuestState: async () => stateWithStatus(401),
      },
    });
    store.getState().attach();
    await settled();

    await store.getState().keepPage();

    expect(host.captureGuest).not.toHaveBeenCalled();
    expect(store.getState().error).toBeTruthy();
  });

  it("lets a rate-limited page be kept, since only the reader knows", async () => {
    const { store, host } = await freshStore({
      host: {
        captureGuest: vi.fn(async () => ({ url: "", html: "" })),
        getGuestState: async () => stateWithStatus(429),
      },
    });
    store.getState().attach();
    await settled();

    await store.getState().keepPage();

    expect(host.captureGuest).toHaveBeenCalled();
    expect(store.getState().error).toBeNull();
  });

  it("hands the serialized page to the workbench, however it was asked for", async () => {
    let report: ((payload: { url: string; html: string }) => void) | undefined;
    const { store } = await freshStore({
      host: {
        onCaptured: (handler: (payload: { url: string; html: string }) => void) => {
          report = handler;
          return () => {};
        },
      },
    });
    store.getState().attach();

    // The shell's own channel is what carries the markup: the request's answer says
    // only whether a capture could be taken.
    report?.({ url: "https://example.com/kept", html: "<html>kept</html>" });
    await Promise.resolve();

    expect(takeRendered).toHaveBeenCalledWith(
      "https://example.com/kept",
      "<html>kept</html>",
    );
  });

  it("does nothing without a shell", async () => {
    const { store } = await freshStore();
    await expect(store.getState().keepPage()).resolves.toBeUndefined();
    expect(store.getState().error).toBeNull();
  });
});
