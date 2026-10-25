import { describe, expect, it, vi } from "vitest";
import {
  activeTab,
  boundsFor,
  hiddenBounds,
  hostOf,
  openInThisBrowser,
  pathOf,
  sameAddress,
  sameBounds,
  tabFor,
  tabLabel,
  webAddress,
} from "@/lib/browse";
import type { GuestTab } from "@/lib/desktop";

function tab(patch: Partial<GuestTab> = {}): GuestTab {
  return {
    id: 1,
    title: "Example",
    url: "https://example.com/",
    loading: false,
    active: true,
    ...patch,
  };
}

describe("where the page goes", () => {
  it("rounds an element's place on screen to whole pixels", () => {
    expect(boundsFor({ left: 12.4, top: 40.6, width: 800.5, height: 600.4 })).toEqual({
      x: 12,
      y: 41,
      width: 801,
      height: 600,
      visible: true,
    });
  });

  it("hides the page when there is nowhere to put it", () => {
    expect(boundsFor(null)).toEqual(hiddenBounds());
    expect(boundsFor(undefined)).toEqual(hiddenBounds());
  });

  it("hides the page when the area is too small to be one", () => {
    // A pane mid-resize or a module still mounting measures as nothing, and a page
    // painted there would cover whatever the reader was looking at.
    expect(boundsFor({ left: 0, top: 0, width: 0, height: 600 })).toEqual(hiddenBounds());
    expect(boundsFor({ left: 0, top: 0, width: 800, height: 0 })).toEqual(hiddenBounds());
    expect(boundsFor({ left: 0, top: 0, width: 31, height: 600 }).visible).toBe(false);
    expect(boundsFor({ left: 0, top: 0, width: 32, height: 32 }).visible).toBe(true);
  });

  it("knows when nothing has moved", () => {
    const area = boundsFor({ left: 8, top: 8, width: 400, height: 300 });
    expect(sameBounds(area, { ...area })).toBe(true);
    expect(sameBounds(area, { ...area, visible: false })).toBe(false);
    expect(sameBounds(area, { ...area, width: 401 })).toBe(false);
    expect(sameBounds(hiddenBounds(), hiddenBounds())).toBe(true);
  });
});

describe("what to open", () => {
  it("reads a plain address as one on the web", () => {
    expect(webAddress("example.com")).toBe("https://example.com/");
    expect(webAddress("  example.com/a?b=1  ")).toBe("https://example.com/a?b=1");
    expect(webAddress("localhost:5174")).toBe("https://localhost:5174/");
    expect(webAddress("example.com:8080/x")).toBe("https://example.com:8080/x");
  });

  it("keeps the scheme a reader wrote", () => {
    expect(webAddress("http://example.com/x")).toBe("http://example.com/x");
    expect(webAddress("https://example.com/x")).toBe("https://example.com/x");
  });

  it("refuses anything that is not a web address", () => {
    // Handing a script or a local file to the guest browser would be a way around
    // the point of it being a browser on the web.
    expect(webAddress("")).toBe(null);
    expect(webAddress("   ")).toBe(null);
    expect(webAddress("javascript:alert(1)")).toBe(null);
    expect(webAddress("mailto:someone@example.com")).toBe(null);
    expect(webAddress("file:///C:/Windows")).toBe(null);
    expect(webAddress("data:text/html,<b>x</b>")).toBe(null);
    // A scheme with nothing after it is not somewhere to go.
    expect(webAddress("https://")).toBe(null);
  });
});

describe("what a tab is called", () => {
  it("prefers the page's own title", () => {
    expect(tabLabel(tab(), "New tab")).toBe("Example");
  });

  it("uses the site when the shell only has the address", () => {
    // The shell sends the address as the title while a page has none, and showing
    // it twice says nothing.
    expect(tabLabel(tab({ title: "", url: "https://example.com/a" }), "New tab")).toBe(
      "example.com",
    );
    expect(
      tabLabel(tab({ title: "https://example.com/a", url: "https://example.com/a" }), "New tab"),
    ).toBe("example.com");
  });

  it("says it is new when it has no page yet", () => {
    expect(tabLabel(tab({ title: "", url: "" }), "New tab")).toBe("New tab");
  });
});

describe("the tab in front", () => {
  it("finds the one the shell names", () => {
    const tabs = [tab({ id: 1 }), tab({ id: 2 })];
    expect(activeTab(tabs, 2)?.id).toBe(2);
  });

  it("is nothing when there are no tabs, or none of them is the named one", () => {
    expect(activeTab([], 1)).toBe(null);
    expect(activeTab(undefined, 1)).toBe(null);
    expect(activeTab([tab({ id: 1 })], 9)).toBe(null);
  });
});

describe("pathOf", () => {
  it("gives where in a site a tab is", () => {
    expect(pathOf("https://example.com/docs/a?q=1")).toBe("/docs/a?q=1");
    expect(pathOf("https://example.com/docs")).toBe("/docs");
  });

  it("gives nothing for a site's front page", () => {
    // Repeating the site under a name that already is the site says nothing.
    expect(pathOf("https://example.com/")).toBe("");
    expect(pathOf("https://example.com")).toBe("");
    expect(pathOf("")).toBe("");
    expect(pathOf("not a url")).toBe("");
  });
});

describe("openInThisBrowser", () => {
  it("opens a new tab, without handing over the opener", () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    openInThisBrowser("https://example.com/");
    expect(open).toHaveBeenCalledWith("https://example.com/", "_blank", "noreferrer");
    vi.unstubAllGlobals();
  });

  it("does nothing where there is no window to open in", () => {
    // Which is how this file's own environment runs.
    expect(() => openInThisBrowser("https://example.com/")).not.toThrow();
  });
});

describe("tabFor", () => {
  it("finds the tab on a page, however the address was written", () => {
    const tabs = [tab({ id: 7, url: "https://example.com/a/" })];
    expect(tabFor(tabs, "https://example.com/a")?.id).toBe(7);
    expect(tabFor(tabs, "https://example.com/a#part")?.id).toBe(7);
  });

  it("says nothing for a page no tab is on", () => {
    const tabs = [tab({ id: 7, url: "https://example.com/a" })];
    expect(tabFor(tabs, "https://example.com/b")).toBeNull();
    expect(tabFor(tabs, "https://other.example/a")).toBeNull();
    expect(tabFor([], "https://example.com/a")).toBeNull();
    expect(tabFor(undefined, "https://example.com/a")).toBeNull();
  });

  it("does not match an empty address to a tab that has none yet", () => {
    // A tab still loading has no address, and an empty address is not a page.
    const tabs = [tab({ id: 7, url: "" }), tab({ id: 8, url: "https://example.com/a" })];
    expect(tabFor(tabs, "")).toBeNull();
    expect(tabFor(tabs, "   ")).toBeNull();
    // Nor is a tab with no address the page being looked for.
    expect(tabFor([tab({ id: 7, url: "" })], "https://example.com/a")).toBeNull();
  });
});

describe("sameAddress", () => {
  it("ignores a trailing slash", () => {
    expect(sameAddress("https://example.com/a", "https://example.com/a/")).toBe(true);
    expect(sameAddress("https://example.com/", "https://example.com")).toBe(true);
  });

  it("ignores the fragment, which is not part of the page", () => {
    expect(sameAddress("https://example.com/a#part", "https://example.com/a")).toBe(true);
  });

  it("keeps the query, which is", () => {
    expect(sameAddress("https://example.com/s?q=1", "https://example.com/s?q=2")).toBe(false);
    expect(sameAddress("https://example.com/s?q=1", "https://example.com/s?q=1")).toBe(true);
  });

  it("tells hosts and schemes apart", () => {
    expect(sameAddress("https://example.com/a", "https://other.example/a")).toBe(false);
    expect(sameAddress("http://example.com/a", "https://example.com/a")).toBe(false);
  });

  it("compares what it cannot parse as text", () => {
    // A captured page may be recorded under a local identity rather than an address.
    expect(sameAddress("local:pdf:abc123", "local:pdf:abc123")).toBe(true);
    expect(sameAddress("local:pdf:abc123", "local:pdf:def456")).toBe(false);
  });

  it("says nothing is the same page as nothing", () => {
    expect(sameAddress("", "")).toBe(true);
    expect(sameAddress("", "https://example.com/")).toBe(false);
  });
});

describe("hostOf", () => {
  it("gives the site, and falls back to what was given", () => {
    expect(hostOf("https://example.com/a/b")).toBe("example.com");
    expect(hostOf("not a url")).toBe("not a url");
  });
});
