import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/notes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/notes")>();
  return {
    ...actual,
    fetchNotes: vi.fn(async () => []),
    fetchProjectNotes: vi.fn(async () => []),
    createNote: vi.fn(),
    updateNote: vi.fn(),
    deleteNote: vi.fn(),
  };
});

import {
  createNote as createApi,
  deleteNote as deleteApi,
  updateNote as updateApi,
  type Note,
} from "@/lib/api/notes";
import { useNotesStore } from "@/stores/notes-store";

/**
 * Writing a remark, and what it does to what has already been read.
 *
 * A write is answered where it was asked for, so the caches that are loaded have
 * to end up as the project now is without reading anything again: the page's own
 * list, and the project-wide one the orphaned remarks are found in. A write that
 * failed has to leave both exactly as they were, because the screen that asked is
 * still showing what it wrote.
 */

const PAGE = "wiki/互信息.md";

function note(over: Partial<Note> & { id: string }): Note {
  return {
    page: PAGE,
    anchor: "定义",
    quote: "",
    created: "2026-10-11T10:00:00+08:00",
    updated: "2026-10-11T10:00:00+08:00",
    body: "写下的内容。",
    ...over,
  };
}

const notes = () => useNotesStore.getState();
const pageNotes = () => notes().pages[PAGE]?.notes.map((item) => item.id) ?? [];
const allNotes = () => notes().all?.notes.map((item) => item.id) ?? [];

beforeEach(() => {
  vi.clearAllMocks();
  useNotesStore.setState({
    project: "p",
    pages: {
      [PAGE]: {
        status: "ready",
        notes: [
          note({ id: "old", created: "2026-10-11T10:00:01+08:00" }),
          note({ id: "newest", created: "2026-10-11T10:00:03+08:00" }),
        ],
        error: null,
      },
    },
    all: {
      status: "ready",
      notes: [
        note({
          id: "lost",
          page: "wiki/改过名的页.md",
          created: "2026-10-11T10:00:00+08:00",
        }),
        note({ id: "old", created: "2026-10-11T10:00:01+08:00" }),
        note({ id: "newest", created: "2026-10-11T10:00:03+08:00" }),
      ],
      error: null,
    },
  });
});

describe("writing a new remark", () => {
  it("lands in the page's list in the place its time gives it", async () => {
    const written = note({ id: "middle", created: "2026-10-11T10:00:02+08:00" });
    vi.mocked(createApi).mockResolvedValue(written);

    await notes().create("p", PAGE, { body: "写下的内容。", anchor: "定义", quote: "" });

    expect(pageNotes()).toEqual(["old", "middle", "newest"]);
    expect(allNotes()).toEqual(["lost", "old", "middle", "newest"]);
  });

  it("does not bring back a page nobody has read", async () => {
    useNotesStore.setState({ pages: {} });
    vi.mocked(createApi).mockResolvedValue(
      note({ id: "middle", created: "2026-10-11T10:00:02+08:00" }),
    );

    await notes().create("p", PAGE, { body: "写下的内容。", anchor: "定义", quote: "" });

    // Nothing was read for that page, so there is no list to keep in step; the
    // next read brings everything.
    expect(notes().pages).toEqual({});
    expect(allNotes()).toEqual(["lost", "old", "middle", "newest"]);
  });

  it("hands a refusal back to whoever asked", async () => {
    vi.mocked(createApi).mockRejectedValue(new Error("page not found: 互信息"));

    await expect(
      notes().create("p", PAGE, { body: "写下的内容。", anchor: "", quote: "" }),
    ).rejects.toThrow("page not found");
    expect(pageNotes()).toEqual(["old", "newest"]);
    expect(allNotes()).toEqual(["lost", "old", "newest"]);
  });
});

describe("changing a remark", () => {
  it("replaces it where it was, in both lists", async () => {
    vi.mocked(updateApi).mockResolvedValue(
      note({ id: "old", body: "改过的话。", updated: "2026-10-11T11:00:00+08:00" }),
    );

    await notes().update("p", "old", { body: "改过的话。", anchor: "定义", quote: "" });

    expect(pageNotes()).toEqual(["old", "newest"]);
    expect(notes().pages[PAGE].notes[0]).toMatchObject({
      id: "old",
      body: "改过的话。",
    });
    expect(allNotes()).toEqual(["lost", "old", "newest"]);
    expect(notes().all?.notes.find((item) => item.id === "old")?.body).toBe("改过的话。");
  });

  it("keeps the lists as they were when the change was refused", async () => {
    vi.mocked(updateApi).mockRejectedValue(new Error("nope"));

    await expect(notes().update("p", "old", { body: "改过的话。" })).rejects.toThrow();
    expect(notes().pages[PAGE].notes[0]).toMatchObject({
      id: "old",
      body: "写下的内容。",
    });
    expect(allNotes()).toEqual(["lost", "old", "newest"]);
  });
});

describe("removing a remark", () => {
  it("drops it from the page and from the project", async () => {
    vi.mocked(deleteApi).mockResolvedValue(true);

    await notes().remove("p", PAGE, "old");

    expect(pageNotes()).toEqual(["newest"]);
    expect(allNotes()).toEqual(["lost", "newest"]);
  });

  it("drops it even when the core says there was nothing to remove", async () => {
    vi.mocked(deleteApi).mockResolvedValue(false);

    await notes().remove("p", PAGE, "old");

    expect(pageNotes()).toEqual(["newest"]);
  });

  it("keeps it when the removal was refused", async () => {
    vi.mocked(deleteApi).mockRejectedValue(new Error("nope"));

    await expect(notes().remove("p", PAGE, "old")).rejects.toThrow();
    expect(pageNotes()).toEqual(["old", "newest"]);
  });
});

describe("reading a page's remarks", () => {
  it("keeps a list that failed out of the way of a write", async () => {
    useNotesStore.setState({
      pages: { [PAGE]: { status: "error", notes: [], error: "读不到" } },
      all: { status: "error", notes: [], error: "读不到" },
    });
    vi.mocked(createApi).mockResolvedValue(note({ id: "middle" }));

    await notes().create("p", PAGE, { body: "写下的内容。", anchor: "", quote: "" });

    // Writing into a list that failed would show a remark beside an error saying
    // the remarks could not be read.
    expect(notes().pages[PAGE].notes).toEqual([]);
    expect(notes().all?.notes).toEqual([]);
  });
});
