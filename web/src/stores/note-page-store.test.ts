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
import { useNotePageStore, watchNotePageProject } from "@/stores/note-page-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * Writing a remark, from the screen's side.
 *
 * There is no save button, so what happens when depends on what leaving looks
 * like: a pause writes, looking away writes, moving on settles. What must never
 * happen is losing what was written — not to an empty moment in the middle of
 * rewriting a remark, and not to a write that failed.
 */

const PAGE = "wiki/互信息.md";
const OTHER = "wiki/熵.md";

function note(over: Partial<Note> & { id: string }): Note {
  return {
    page: PAGE,
    anchor: "定义",
    quote: "",
    created: "2026-10-11T10:00:00+08:00",
    updated: "2026-10-11T10:00:00+08:00",
    body: "原本写的话。",
    ...over,
  };
}

const EXISTING = note({ id: "n1" });

const page = () => useNotePageStore.getState();
const notes = () => useNotesStore.getState();
const drafted = () => page().draft;

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: "p" });
  useNotePageStore.setState({
    project: "p",
    target: PAGE,
    selectedId: null,
    draft: null,
    saving: false,
    error: null,
  });
  useNotesStore.setState({
    project: "p",
    pages: {
      [PAGE]: { status: "ready", notes: [EXISTING], error: null },
      [OTHER]: { status: "ready", notes: [], error: null },
    },
    all: { status: "ready", notes: [EXISTING], error: null },
  });
});

describe("writing something new", () => {
  it("keeps the draft open once it is written, so the next sentence changes it", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );

    await page().startNew();
    expect(drafted()).toMatchObject({ id: null, body: "", page: PAGE });

    page().setBody("写下的第一句话。");
    await page().save();

    expect(createApi).toHaveBeenCalledTimes(1);
    // Still open, and now the remark that exists: typing on changes it rather
    // than writing a second one.
    expect(drafted()).toMatchObject({ id: "new", body: "写下的第一句话。" });

    await page().save();
    expect(updateApi).not.toHaveBeenCalled();
    expect(createApi).toHaveBeenCalledTimes(1);
  });

  it("leaves nothing behind when nothing was ever written", async () => {
    await page().startNew();
    expect(await page().finish()).toBe(true);

    expect(createApi).not.toHaveBeenCalled();
    expect(drafted()).toBeNull();
    expect(page().selectedId).toBeNull();
  });

  it("writes it without closing it, then reads it back where it was put", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );

    await page().startNew();
    page().setBody("一句话。");
    page().setPlace({ anchor: "计算式", quote: "离散情形按求和计算" });
    await page().finish();

    expect(createApi).toHaveBeenCalledWith("p", PAGE, {
      body: "一句话。",
      anchor: "计算式",
      quote: "离散情形按求和计算",
    });
    expect(drafted()).toBeNull();
    expect(page().selectedId).toBe("new");
    expect(page().error).toBeNull();
  });
});

describe("changing a remark that is already written", () => {
  it("keeps a pause from removing it, and removes it when the writer leaves", async () => {
    vi.mocked(deleteApi).mockResolvedValue(true);

    await page().startEdit(EXISTING);
    page().setBody("");
    await page().save();

    // A pause can be the middle of replacing every word, so nothing is removed
    // until the writer has actually gone.
    expect(deleteApi).not.toHaveBeenCalled();
    expect(drafted()).toMatchObject({ id: "n1", body: "" });

    await page().finish();
    expect(deleteApi).toHaveBeenCalledWith("p", "n1");
    expect(drafted()).toBeNull();
    expect(page().selectedId).toBeNull();
    expect(notes().pages[PAGE].notes).toEqual([]);
  });

  it("writes the new text when the pause comes after it", async () => {
    vi.mocked(updateApi).mockImplementation(async (_project, id, fields) =>
      note({ id, ...fields }),
    );

    await page().startEdit(EXISTING);
    page().setBody("改过的话。");
    await page().save();

    expect(updateApi).toHaveBeenCalledWith("p", "n1", {
      body: "改过的话。",
      anchor: "定义",
      quote: "",
    });
    expect(drafted()).toMatchObject({ id: "n1", body: "改过的话。" });
  });

  it("lets the draft go without writing when it is given up", async () => {
    await page().startEdit(EXISTING);
    page().setBody("改了但不想要了。");
    page().discard();

    expect(updateApi).not.toHaveBeenCalled();
    expect(drafted()).toBeNull();
    expect(notes().pages[PAGE].notes[0].body).toBe("原本写的话。");
  });
});

describe("leaving a remark", () => {
  it("writes what was written before moving to another one", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );

    await page().startNew();
    page().setBody("写下的第一句话。");
    await page().select(EXISTING.id);

    expect(createApi).toHaveBeenCalledTimes(1);
    expect(page().selectedId).toBe("n1");
    expect(drafted()).toBeNull();
  });

  it("does not move on when the write failed, so nothing written is lost", async () => {
    vi.mocked(createApi).mockRejectedValue(new Error("存不下"));

    await page().startNew();
    page().setBody("写下的第一句话。");
    await page().select(EXISTING.id);

    expect(page().selectedId).toBeNull();
    expect(drafted()).toMatchObject({ id: null, body: "写下的第一句话。" });
    expect(page().error).toBe(i18n.t("remark.saveFailed"));
    expect(page().saving).toBe(false);
  });

  it("shows the page's remarks when another view asks for them, with one in front", async () => {
    await page().openFor(OTHER, "n9");

    expect(page().target).toBe(OTHER);
    expect(page().selectedId).toBe("n9");
    expect(drafted()).toBeNull();
  });
});

/**
 * Leaving happens in more than one move at once.
 *
 * A writer who types and then reaches for the button stops writing and presses it
 * in the same moment: the editor losing focus writes the remark, and the button
 * writes it again. Nothing may be written twice, nothing typed may be lost, and the
 * remark the writer was working on has to still be in front afterwards.
 */
describe("leaving a remark in more than one move", () => {
  it("writes it once when the pause and the leaving arrive together", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );

    await page().startNew();
    page().setBody("写下的一句话。");
    const paused = page().save();
    const left = page().finish();
    await Promise.all([paused, left]);

    expect(createApi).toHaveBeenCalledTimes(1);
    expect(drafted()).toBeNull();
    expect(page().selectedId).toBe("new");
  });

  it("keeps the remark in front when the writer changes nothing and leaves", async () => {
    await page().startEdit(EXISTING);

    expect(await page().finish()).toBe(true);
    expect(updateApi).not.toHaveBeenCalled();
    expect(page().selectedId).toBe("n1");
    expect(drafted()).toBeNull();
  });

  it("keeps what is typed after a write is on its way", async () => {
    let release!: () => void;
    vi.mocked(createApi).mockImplementation(
      (_project, _page, fields) =>
        new Promise<Note>((resolve) => {
          release = () => resolve(note({ id: "new", ...fields }));
        }),
    );

    await page().startNew();
    page().setBody("第一句。");
    const saving = page().save();
    // The write is handed out a moment later, so the writer has typed on by the
    // time it is on its way, and the answer is about older text than what they
    // have in front of them.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(createApi).toHaveBeenCalledTimes(1);
    page().setBody("第一句。第二句。");
    release();
    await saving;

    expect(createApi).toHaveBeenCalledWith("p", PAGE, {
      body: "第一句。",
      anchor: "",
      quote: "",
    });
    expect(drafted()).toMatchObject({ id: "new", body: "第一句。第二句。" });
  });
});

/**
 * A remark belongs to the wiki its page is in.
 *
 * The screen follows whichever project is active, and a remark can be left open
 * while the reader switches. What was written must land in the project it was
 * written about — not in the one that happens to be in front when a timer fires.
 */
describe("a remark belongs to the project it was begun in", () => {
  it("is written into that project even after the screen has moved on", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );

    useNotePageStore.setState({ target: null, project: null });
    await page().openFor(PAGE);
    expect(page().project).toBe("p");

    await page().startNew();
    page().setBody("对旧项目说的话。");
    useWorkspaceStore.setState({ current: "q" });
    await page().save();

    expect(createApi).toHaveBeenCalledWith("p", PAGE, {
      body: "对旧项目说的话。",
      anchor: "",
      quote: "",
    });
  });

  it("is settled before the next project is shown", async () => {
    vi.mocked(createApi).mockImplementation(async (_project, _page, fields) =>
      note({ id: "new", ...fields }),
    );
    const stop = watchNotePageProject();
    try {
      useNotePageStore.setState({ target: null, project: null });
      await page().openFor(PAGE);
      await page().startNew();
      page().setBody("先存下这句。");

      useWorkspaceStore.setState({ current: "q" });

      await vi.waitFor(() => {
        expect(createApi).toHaveBeenCalledWith("p", PAGE, {
          body: "先存下这句。",
          anchor: "",
          quote: "",
        });
      });
      await vi.waitFor(() => expect(page().project).toBeNull());
    } finally {
      stop();
    }
  });

  it("keeps the draft open when it could not be written", async () => {
    vi.mocked(createApi).mockRejectedValue(new Error("存不下"));
    const stop = watchNotePageProject();
    try {
      useNotePageStore.setState({ target: null, project: null });
      await page().openFor(PAGE);
      await page().startNew();
      page().setBody("不能丢的一句。");

      useWorkspaceStore.setState({ current: "q" });

      await vi.waitFor(() => expect(page().error).toBe(i18n.t("remark.saveFailed")));
      expect(drafted()).toMatchObject({ body: "不能丢的一句。" });
      expect(page().project).toBe("p");
    } finally {
      stop();
    }
  });
});
