// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import type { ChatConversation } from "@/lib/api/types";
import { useChatStore } from "@/stores/chat-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/chat", () => ({
  fetchChats: vi.fn(),
  fetchChat: vi.fn(),
  createChat: vi.fn(),
  renameChat: vi.fn(),
  deleteChat: vi.fn(),
  queryChat: vi.fn(),
  fetchChatLimits: vi.fn(),
  putChatLimits: vi.fn(),
}));

import { deleteChat, renameChat } from "@/lib/api/chat";

/**
 * The conversation list, as a reader uses it: find one by name, name one, delete one.
 *
 * These are the acts a list of conversations is for once it is longer than the pane
 * can show, and each has a way of going wrong that the reader would only notice
 * afterwards — a search that hides the conversation in front, a rename that does not
 * stick, a delete that happens before it was meant.
 */

let host: HTMLDivElement;
let root: Root;

function conversation(id: string, title: string): ChatConversation {
  return { id, title, updatedAt: "2026-10-20T00:00:00Z", messages: [] };
}

function state(title = "会话") {
  return {
    project: "p",
    status: "ready" as const,
    error: null,
    conversations: [
      { id: "c1", title, updatedAt: "2026-10-20T00:00:00Z", messageCount: 2 },
      { id: "c2", title: "另一个", updatedAt: "2026-10-19T00:00:00Z", messageCount: 1 },
    ],
    limits: null,
    limitsDraft: null,
    limitsBounds: null,
    settingsOpen: false,
    savingLimits: false,
    limitsError: null,
    currentId: "c1",
    open: conversation("c1", title),
    draft: "",
    pending: null,
    looked: [],
    turnError: null,
    failedQuestion: null,
    stopped: false,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: "p" });
  useChatStore.setState(state());
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

async function draw() {
  await act(async () => {
    root.render(<ChatSidebar />);
  });
}

/** The buttons in the list, in the order they are shown. */
function rows(): HTMLElement[] {
  return [...host.querySelectorAll("li")];
}

function button(name: string): HTMLButtonElement {
  // A row's controls are icons, so their name is in the label rather than the text.
  const found = [...host.querySelectorAll("button")].find(
    (item) => item.getAttribute("aria-label") === name || item.textContent?.includes(name),
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found as HTMLButtonElement;
}

function searchBox(): HTMLInputElement {
  const found = host.querySelector<HTMLInputElement>(`input[aria-label="${i18n.t("chat.search")}"]`);
  if (!found) throw new Error("no search box");
  return found;
}

function renameBox(): HTMLInputElement {
  const found = host.querySelector<HTMLInputElement>(`input[aria-label="${i18n.t("chat.rename")}"]`);
  if (!found) throw new Error("no rename box");
  return found;
}

/** Types into a controlled input the way a reader would. */
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function press(input: HTMLInputElement, key: string) {
  input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
}

describe("finding a conversation", () => {
  it("keeps only the ones that match", async () => {
    await draw();
    expect(rows()).toHaveLength(2);

    act(() => type(searchBox(), "另一个"));

    expect(rows()).toHaveLength(1);
    expect(host.textContent).toContain("另一个");
    expect(host.textContent).not.toContain("会话");
  });

  it("says so when nothing matches, rather than looking empty", async () => {
    await draw();
    act(() => type(searchBox(), "没有这个"));

    expect(rows()).toHaveLength(0);
    expect(host.textContent).toContain(i18n.t("chat.searchEmpty"));
  });

  it("finds an unnamed conversation by the name it is shown under", async () => {
    useChatStore.setState(state(""));
    await draw();

    act(() => type(searchBox(), i18n.t("chat.untitled")));

    expect(rows()).toHaveLength(1);
  });
});

describe("naming a conversation", () => {
  it("renames where the name is, with one field and no dialog", async () => {
    vi.mocked(renameChat).mockResolvedValue(conversation("c1", "熵的讨论"));
    await draw();

    act(() => button(i18n.t("chat.rename")).click());
    const field = renameBox();
    expect(field.value).toBe("会话");

    act(() => type(field, "熵的讨论"));
    await act(async () => {
      press(field, "Enter");
    });

    expect(renameChat).toHaveBeenCalledWith("p", "c1", "熵的讨论");
    expect(useChatStore.getState().conversations[0].title).toBe("熵的讨论");
    expect(host.textContent).toContain("熵的讨论");
    expect(host.querySelector(`input[aria-label="${i18n.t("chat.rename")}"]`)).toBeNull();
  });

  it("starts from the title too, for a reader who tries the obvious thing", async () => {
    await draw();

    act(() => {
      const title = [...host.querySelectorAll("li button")].find((item) =>
        item.textContent?.includes("会话"),
      );
      title?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });

    expect(renameBox().value).toBe("会话");
  });

  it("does not send a name that did not change", async () => {
    await draw();

    act(() => button(i18n.t("chat.rename")).click());
    await act(async () => {
      press(renameBox(), "Enter");
    });

    expect(renameChat).not.toHaveBeenCalled();
  });

  it("leaves the name alone when the reader gives up", async () => {
    await draw();

    act(() => button(i18n.t("chat.rename")).click());
    act(() => type(renameBox(), "不要这个名字"));
    await act(async () => {
      press(renameBox(), "Escape");
    });

    expect(renameChat).not.toHaveBeenCalled();
    expect(host.textContent).toContain("会话");
    expect(host.textContent).not.toContain("不要这个名字");
  });

  it("sends the trimmed name", async () => {
    vi.mocked(renameChat).mockResolvedValue(conversation("c1", "熵的讨论"));
    await draw();

    act(() => button(i18n.t("chat.rename")).click());
    act(() => type(renameBox(), "  熵的讨论  "));
    await act(async () => {
      press(renameBox(), "Enter");
    });

    expect(renameChat).toHaveBeenCalledWith("p", "c1", "熵的讨论");
  });
});

describe("deleting a conversation", () => {
  it("asks first, by name", async () => {
    await draw();

    act(() => button(i18n.t("chat.remove")).click());

    // The dialog is drawn by the page, not by the list.
    expect(document.body.textContent).toContain(
      i18n.t("chat.delete.title", { title: "会话" }),
    );
    expect(deleteChat).not.toHaveBeenCalled();
  });

  it("deletes only once the reader says so", async () => {
    vi.mocked(deleteChat).mockResolvedValue({
      limits: {
        historyLimit: 10,
        maxMessages: 200,
        maxContentChars: 8_000,
        maxConversations: 40,
        maxTitleChars: 80,
        maxBriefChars: 6_000,
        maxIndexChars: 12_000,
        maxOpenPages: 6,
        maxPageChars: 8_000,
        maxPagesChars: 24_000,
        maxToolRounds: 6,
      },
      conversations: [
        { id: "c2", title: "另一个", updatedAt: "2026-10-19T00:00:00Z", messageCount: 1 },
      ],
    });
    await draw();

    act(() => button(i18n.t("chat.remove")).click());
    const confirm = [...document.body.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("chat.delete.submit")),
    );
    await act(async () => {
      confirm?.click();
    });

    expect(deleteChat).toHaveBeenCalledWith("p", "c1");
    expect(useChatStore.getState().conversations.map((item) => item.id)).toEqual(["c2"]);
  });

  it("closes without deleting when the reader backs out", async () => {
    await draw();

    act(() => button(i18n.t("chat.remove")).click());
    const cancel = [...document.body.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("common.cancel")),
    );
    await act(async () => {
      cancel?.click();
    });

    expect(deleteChat).not.toHaveBeenCalled();
  });
});
