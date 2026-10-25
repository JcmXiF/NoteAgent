// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SaveRemark } from "@/components/chat/SaveRemark";
import { createNote } from "@/lib/api/notes";
import { useDocsStore } from "@/stores/docs-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/notes", () => ({
  fetchNotes: vi.fn(),
  fetchProjectNotes: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}));

/**
 * Keeping an answer.
 *
 * A remark belongs to a page, so what this has to get right is which page — the
 * one in front — and what it quotes; everything else is the remarks module's own
 * business and is only asked for here.
 */

const page = "wiki/concepts/entropy.md";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: "p" });
  useDocsStore.setState({
    files: [{ path: page, name: "entropy.md", title: "熵" }],
    paths: { main: page, side: null },
  });
  useNotesStore.getState().reset("p");
  useNavStore.setState({ seq: 0, intent: null });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
});

async function draw(question: string | null = "什么是熵") {
  await act(async () => {
    root.render(
      <SaveRemark answer="熵是信息的平均量。" question={question} slot="side" />,
    );
  });
  await act(async () => {});
}

function button(label: string): HTMLButtonElement | null {
  return (
    [...host.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(label),
    ) ?? null
  );
}

describe("saving an answer as a remark", () => {
  it("writes it on the page in front, quoting the question", async () => {
    vi.mocked(createNote).mockImplementation(async (_project, about, fields) => ({
      id: "n-1",
      page: about,
      body: fields.body,
      anchor: fields.anchor,
      quote: fields.quote,
      created: "2026-10-20T00:00:00Z",
      updated: "2026-10-20T00:00:00Z",
    }));

    await draw();
    const keep = button(i18n.t("chat.remark.save"));
    expect(keep).not.toBeNull();
    expect(keep?.disabled).toBe(false);

    await act(async () => {
      keep?.click();
    });

    expect(vi.mocked(createNote)).toHaveBeenCalledWith("p", page, {
      body: "熵是信息的平均量。",
      anchor: "",
      quote: "什么是熵",
    });
    expect(host.textContent).toContain(i18n.t("chat.remark.saved"));
  });

  it("opens the remark beside the conversation once it is kept", async () => {
    vi.mocked(createNote).mockResolvedValue({
      id: "n-2",
      page,
      body: "答",
      anchor: "",
      quote: "",
      created: "2026-10-20T00:00:00Z",
      updated: "2026-10-20T00:00:00Z",
    });

    await draw();
    await act(async () => {
      button(i18n.t("chat.remark.save"))?.click();
    });

    act(() => {
      button(i18n.t("chat.remark.open"))?.click();
    });

    expect(useNavStore.getState().intent).toEqual({
      kind: "notes",
      path: page,
      noteId: "n-2",
    });
  });

  it("cannot be kept with no page in front, and says why", async () => {
    useDocsStore.setState({ paths: { main: null, side: null }, files: [] });

    await draw();

    const keep = button(i18n.t("chat.remark.save"));
    expect(keep?.disabled).toBe(true);
    expect(keep?.getAttribute("title")).toBe(i18n.t("chat.remark.needPage"));
  });

  it("does not write a remark about a stored original", async () => {
    useDocsStore.setState({
      paths: { main: "raw/web/entropy.html", side: null },
      files: [{ path: "raw/web/entropy.html", name: "entropy.html", title: "Entropy" }],
    });

    await draw();

    expect(button(i18n.t("chat.remark.save"))?.disabled).toBe(true);
    expect(vi.mocked(createNote)).not.toHaveBeenCalled();
  });

  it("says so when it cannot be kept, and stays where it was", async () => {
    vi.mocked(createNote).mockRejectedValue(new Error("disk full"));

    await draw();
    await act(async () => {
      button(i18n.t("chat.remark.save"))?.click();
    });

    expect(host.textContent).toContain(i18n.t("chat.remark.saveFailed"));
    expect(button(i18n.t("chat.remark.save"))).not.toBeNull();
  });
});
