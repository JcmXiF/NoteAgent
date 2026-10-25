// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ChatView } from "@/components/chat/ChatView";
import type { ChatConversation, ChatTurn } from "@/lib/api/types";
import { useChatStore } from "@/stores/chat-store";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsStore } from "@/stores/docs-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/notes", () => ({
  createNote: vi.fn(),
  fetchNotes: vi.fn(),
  fetchProjectNotes: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}));

/**
 * A conversation, as it is read the next day.
 *
 * An answer names pages in prose; the rows under it are what make those pages
 * openable. They used to exist only while the turn was on screen, so reopening a
 * conversation left the answer with no way back to what it was built from.
 */

let host: HTMLDivElement;
let root: Root;

function turn(content: string, role: ChatTurn["role"], cards?: ChatTurn["cards"]): ChatTurn {
  return {
    id: `${role}-${content}`,
    role,
    content,
    createdAt: "2026-10-20T00:00:00Z",
    ...(cards ? { cards } : {}),
  };
}

function conversation(messages: ChatTurn[]): ChatConversation {
  return { id: "c1", title: "会话", updatedAt: "2026-10-20T00:00:00Z", messages };
}

beforeEach(() => {
  useWorkspaceStore.setState({ current: "p" });
  useDocsStore.setState({
    files: [{ path: "wiki/concepts/entropy.md", name: "entropy.md", title: "熵" }],
    paths: { main: "wiki/concepts/entropy.md", side: null },
  });
  useClipsStore.setState({ collections: [], topicMap: null, decisions: {} });
  useChatStore.setState({
    project: "p",
    status: "ready",
    error: null,
    conversations: [],
    currentId: "c1",
    open: conversation([]),
    draft: "",
    pending: null,
    looked: [],
    turnError: null,
    failedQuestion: null,
    stopped: false,
  });
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

async function draw(messages: ChatTurn[]) {
  useChatStore.setState({ open: conversation(messages) });
  await act(async () => {
    root.render(<ChatView slot="side" />);
  });
  await act(async () => {});
}

describe("a conversation reopened", () => {
  it("still shows what the answer was built from", async () => {
    await draw([
      turn("什么是熵", "user"),
      turn("熵是信息的平均量。", "assistant", [
        { tool: "wiki_read", title: "熵", path: "wiki/concepts/entropy.md" },
        { tool: "search", title: "熵与信息", scope: "wiki", path: "wiki/concepts/info.md" },
      ]),
    ]);

    const rows = host.querySelector(`section[aria-label="${i18n.t("chat.looked.title")}"]`);
    expect(rows).not.toBeNull();
    expect(rows?.textContent).toContain("熵与信息");
  });

  it("offers to keep the answer as a remark", async () => {
    await draw([turn("什么是熵", "user"), turn("熵是信息的平均量。", "assistant")]);

    expect(
      [...host.querySelectorAll("button")].some((item) =>
        item.textContent?.includes(i18n.t("chat.remark.save")),
      ),
    ).toBe(true);
  });

  it("reads an answer from before cards without inventing any", async () => {
    await draw([turn("什么是熵", "user"), turn("熵是信息的平均量。", "assistant")]);

    expect(host.querySelector(`section[aria-label="${i18n.t("chat.looked.title")}"]`)).toBeNull();
  });
});
