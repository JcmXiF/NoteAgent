import { afterEach, describe, expect, it, vi } from "vitest";
import {
  canPickFolder,
  desktopBridge,
  guestCaptureBlocked,
  hasDesktop,
  pickFolder,
  type NoteagentDesktop,
} from "@/lib/desktop";

/**
 * The whole point of this module is that a page cannot tell whether it is in the
 * desktop app until it asks, and that asking is never a failure. The tests run in a
 * plain Node environment, which is the "no shell at all" case.
 */

/** A host offering only what every shell must offer. */
const HOST: NoteagentDesktop = {
  openGuest: async () => {},
  captureGuest: async () => ({ url: "", html: "" }),
  onGuestUrl: () => () => {},
  onCaptured: () => () => {},
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("without a shell", () => {
  it("answers with nothing rather than failing", () => {
    // No `window` at all, which is how this file's own environment starts.
    expect(desktopBridge()).toBeUndefined();
    expect(hasDesktop()).toBe(false);
  });

  it("answers the same way in a browser that has no shell behind it", () => {
    vi.stubGlobal("window", {});
    expect(desktopBridge()).toBeUndefined();
    expect(hasDesktop()).toBe(false);
  });
});

describe("with a shell", () => {
  it("hands back the interface the shell exposes", () => {
    vi.stubGlobal("window", { noteagentDesktop: HOST });
    expect(desktopBridge()).toBe(HOST);
    expect(hasDesktop()).toBe(true);
  });

  it("accepts a shell that offers only the required part", () => {
    // Most of the interface is optional, because a page has to work in a browser;
    // a host is not required to have grown every part yet.
    vi.stubGlobal("window", { noteagentDesktop: HOST });
    const bridge = desktopBridge();
    expect(bridge?.setGuestBounds).toBeUndefined();
    expect(bridge?.onGuestState).toBeUndefined();
  });

  it("ignores something that is not the interface", () => {
    // Anything can be put on `window`; taking it for a bridge would turn a mistake
    // into a crash somewhere else.
    for (const value of ["yes", 42, true]) {
      vi.stubGlobal("window", { noteagentDesktop: value });
      expect(desktopBridge()).toBeUndefined();
      expect(hasDesktop()).toBe(false);
    }
  });
});

describe("guestCaptureBlocked", () => {
  it("is true when the site asked the reader to sign in", () => {
    expect(guestCaptureBlocked(401)).toBe(true);
    expect(guestCaptureBlocked(403)).toBe(true);
  });

  it("is false for anything else, including no answer at all", () => {
    for (const status of [200, 204, 404, 429, 500, null, undefined]) {
      expect(guestCaptureBlocked(status)).toBe(false);
    }
  });
});

describe("choosing a folder", () => {
  it("is not offered where there is no picker to open", async () => {
    // A browser has no way to read a directory out of a file input, so the dialog
    // asks before drawing a button that would do nothing.
    vi.stubGlobal("window", { noteagentDesktop: HOST });
    expect(canPickFolder()).toBe(false);
    expect(await pickFolder()).toBeNull();
  });

  it("answers with the folder the shell's picker returned", async () => {
    const pick = vi.fn(async () => "D:\\notes\\my-wiki");
    vi.stubGlobal("window", { noteagentDesktop: { ...HOST, pickFolder: pick } });
    expect(canPickFolder()).toBe(true);
    expect(await pickFolder()).toBe("D:\\notes\\my-wiki");
  });

  it("treats an empty answer as nothing chosen rather than a failure", async () => {
    // Closing the picker is an answer; the path field stays usable.
    vi.stubGlobal("window", {
      noteagentDesktop: { ...HOST, pickFolder: async () => "" },
    });
    expect(await pickFolder()).toBeNull();
  });

  it("does not let a broken picker take the dialog down with it", async () => {
    vi.stubGlobal("window", {
      noteagentDesktop: {
        ...HOST,
        pickFolder: async () => {
          throw new Error("no such window");
        },
      },
    });
    expect(await pickFolder()).toBeNull();
  });
});
