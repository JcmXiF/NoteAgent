import { beforeEach, describe, expect, it, vi } from "vitest";

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

import {
  deleteChat,
  fetchChat,
  fetchChatLimits,
  putChatLimits,
  queryChat,
  renameChat,
} from "@/lib/api/chat";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import type { ChatConversation, ChatTurn } from "@/lib/api/types";
import i18n from "@/i18n";

function turn(content: string, role: ChatTurn["role"] = "user"): ChatTurn {
  return { id: content, role, content, createdAt: "2026-09-19T00:00:00Z" };
}

function conversation(messages: ChatTurn[] = []): ChatConversation {
  return { id: "c1", title: "会话", updatedAt: "2026-09-19T00:00:00Z", messages };
}

const LIMITS = {
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
};

/** The ranges the core sends alongside the values. */
const BOUNDS = Object.fromEntries(
  Object.keys(LIMITS).map((key) => [key, { min: 0, max: 1_000_000 }]),
) as Record<keyof typeof LIMITS, { min: number; max: number }>;

/** Lets the shell's answer land before an action that depends on it. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({
    project: "p",
    status: "ready",
    error: null,
    conversations: [
      { id: "c1", title: "会话", updatedAt: "2026-09-19T00:00:00Z", messageCount: 0 },
    ],
    limits: LIMITS,
    limitsDraft: LIMITS,
    limitsBounds: BOUNDS,
    settingsOpen: false,
    savingLimits: false,
    limitsError: null,
    currentId: "c1",
    open: conversation(),
    draft: "什么是熵",
    pending: null,
    failedQuestion: null,
    turnError: null,
    stopped: false,
  });
  useActivityStore.setState({ items: [] });
});

describe("the turn in the jobs panel", () => {
  it("shows the phase the turn has reached, and never goes back", async () => {
    const seen: { steps: string[]; message?: string; detail: string[] }[] = [];
    vi.mocked(queryChat).mockImplementation(async (_project, _id, _content, options = {}) => {
      options.onStatus?.("正在思考是否需要打开、搜索、检查或规划笔记", "think");
      options.onStatus?.("正在打开命中页（1）", "tool");
      // The core says it is thinking again after the tool returned.
      options.onStatus?.("正在根据工具结果继续", "think");
      const mid = useActivityStore.getState().items[0];
      seen.push({
        steps: (mid.steps ?? []).map((step) => step.state),
        message: mid.message,
        detail: mid.detail,
      });
      // The answer starts arriving: the track moves to the answering phase even
      // though the core will not name it until it saves.
      options.onToken?.("信息");
      const writing = useActivityStore.getState().items[0];
      seen.push({
        steps: (writing.steps ?? []).map((step) => step.state),
        message: writing.message,
        detail: writing.detail,
      });
      return conversation([turn("什么是熵"), turn("答案", "assistant")]);
    });

    await useChatStore.getState().ask("什么是熵");

    // Mid-turn the track sits on the tool phase: a turn that thinks again after a
    // tool has not gone backwards.
    expect(seen[0].steps).toEqual(["done", "current", "pending"]);
    // The core's wording is for the log; the screen was given a sentence of ours.
    expect(seen[0].message).not.toBe("正在根据工具结果继续");
    expect(seen[0].message).toBeTruthy();
    expect(seen[0].detail).toContain("正在根据工具结果继续 [think]");
    // Writing the answer is what it says while the answer is arriving.
    expect(seen[1].steps).toEqual(["done", "done", "current"]);
    expect(seen[1].message).not.toBe(seen[0].message);

    const done = useActivityStore.getState().items[0];
    expect(done.kind).toBe("chat");
    expect(done.phase).toBe("done");
    expect(done.steps?.map((step) => step.state)).toEqual(["done", "done", "done"]);
  });

  it("ends as answered, with the phases the reader would expect", async () => {
    vi.mocked(queryChat).mockImplementation(async () => conversation([]));
    await useChatStore.getState().ask("什么是熵");
    const item = useActivityStore.getState().items[0];
    expect(item.steps?.map((step) => step.id)).toEqual(["think", "tool", "answer"]);
    expect(item.steps?.every((step) => step.label.length > 0)).toBe(true);
    expect(item.message).toBeTruthy();
  });

  it("is marked cancelled when the reader stops it", async () => {
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.onToken?.("答");
          // What fetch does when its signal is aborted.
          options.signal?.addEventListener("abort", () => {
            const err = new Error("stopped");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    vi.mocked(fetchChat).mockResolvedValue(conversation([turn("什么是熵")]));

    const asking = useChatStore.getState().ask("什么是熵");
    await Promise.resolve();
    useChatStore.getState().stop();
    await asking;

    const item = useActivityStore.getState().items[0];
    expect(item.phase).toBe("cancelled");
    // The row describes work in progress, so it does not keep saying something.
    expect(item.message).toBeUndefined();
  });
});

describe("stopping a turn", () => {
  it("keeps the question, drops the half-written answer, and says what happened", async () => {
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.onToken?.("答到一半");
          options.signal?.addEventListener("abort", () => {
            const err = new Error("stopped");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    // The core saved the question before it started answering, which is why the
    // turn is left with a question and no answer.
    vi.mocked(fetchChat).mockResolvedValue(conversation([turn("什么是熵")]));

    const asking = useChatStore.getState().ask("什么是熵");
    await Promise.resolve();
    expect(useChatStore.getState().pending?.answer).toBe("答到一半");
    useChatStore.getState().stop();
    await asking;

    const state = useChatStore.getState();
    expect(state.pending).toBeNull();
    expect(state.open?.messages).toHaveLength(1);
    expect(state.stopped).toBe(true);
    // A stop is not a failure, so nothing is reported as one.
    expect(state.turnError).toBeNull();
    // The question belongs to the project now, so it is not handed back.
    expect(state.draft).toBe("");
  });

  it("hands the question back if the core never kept it", async () => {
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            const err = new Error("stopped");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    vi.mocked(fetchChat).mockResolvedValue(conversation());

    const asking = useChatStore.getState().ask("什么是熵");
    await Promise.resolve();
    useChatStore.getState().stop();
    await asking;

    expect(useChatStore.getState().draft).toBe("什么是熵");
    expect(useChatStore.getState().stopped).toBe(true);
  });

  it("forgets the stop on the next question", async () => {
    vi.mocked(queryChat).mockImplementation(async () =>
      conversation([turn("新的问题"), turn("答案", "assistant")]),
    );
    useChatStore.setState({ stopped: true });
    await useChatStore.getState().ask("新的问题");
    expect(useChatStore.getState().stopped).toBe(false);
  });

  it("stops a turn that is running when the reader moves on", async () => {
    let aborted = false;
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            aborted = true;
            const err = new Error("stopped");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    vi.mocked(fetchChat).mockResolvedValue(conversation());

    const asking = useChatStore.getState().ask("什么是熵");
    await Promise.resolve();
    await useChatStore.getState().openConversation("c2");
    await asking;

    expect(aborted).toBe(true);
    expect(useChatStore.getState().pending).toBeNull();
    // The abandoned turn belongs to a conversation that is no longer in front, so
    // nothing is reported about it where the reader now is.
    expect(useChatStore.getState().stopped).toBe(false);
  });
});

describe("the settings area", () => {
  const saved = { ...LIMITS, historyLimit: 10 };

  it("reads the limits when it is opened, and keeps them out of the way until then", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({
      ...saved,
      historyLimit: 7,
      bounds: BOUNDS,
    });

    expect(useChatStore.getState().settingsOpen).toBe(false);
    expect(useChatStore.getState().limitsDraft).toEqual(LIMITS);

    useChatStore.getState().setSettingsOpen(true);
    expect(useChatStore.getState().settingsOpen).toBe(true);
    await settled();

    // Open means showing what the core has, which is the one moment it claims to.
    expect(fetchChatLimits).toHaveBeenCalledWith("p");
    expect(useChatStore.getState().limitsDraft).toEqual({ ...saved, historyLimit: 7 });
    expect(useChatStore.getState().limits).toEqual({ ...saved, historyLimit: 7 });
    // The ranges come with the values, so the panel never keeps its own copy.
    expect(useChatStore.getState().limitsBounds).toEqual(BOUNDS);
  });

  it("keeps a change as a draft until it is saved", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({ ...saved, bounds: BOUNDS });
    vi.mocked(putChatLimits).mockResolvedValue({ ...saved, maxToolRounds: 9, bounds: BOUNDS });

    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    useChatStore.getState().setLimit("maxToolRounds", 9);
    expect(useChatStore.getState().limitsDraft?.maxToolRounds).toBe(9);
    expect(useChatStore.getState().limits?.maxToolRounds).toBe(6);
    expect(putChatLimits).not.toHaveBeenCalled();

    await useChatStore.getState().saveLimits();

    expect(putChatLimits).toHaveBeenCalledWith("p", { ...saved, maxToolRounds: 9 });
    // Both sides become what the core reports, so the panel is clean again.
    expect(useChatStore.getState().limits).toEqual({ ...saved, maxToolRounds: 9 });
    expect(useChatStore.getState().limitsDraft).toEqual({ ...saved, maxToolRounds: 9 });
    expect(useChatStore.getState().savingLimits).toBe(false);
  });

  it("shows what the core reports after saving, not what was sent", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({ ...saved, bounds: BOUNDS });
    // The core clamps to its own bounds; that answer is the truth.
    vi.mocked(putChatLimits).mockResolvedValue({ ...saved, maxPagesChars: 24_000, bounds: BOUNDS });

    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    useChatStore.getState().setLimit("maxPagesChars", 200_000);
    await useChatStore.getState().saveLimits();

    expect(useChatStore.getState().limitsDraft?.maxPagesChars).toBe(24_000);
  });

  it("throws the draft away and shows what the project has", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({ ...saved, bounds: BOUNDS });
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    useChatStore.getState().setLimit("historyLimit", 3);
    useChatStore.getState().discardLimits();

    expect(useChatStore.getState().limitsDraft).toEqual(saved);
    expect(putChatLimits).not.toHaveBeenCalled();
  });

  it("does not throw away an unsaved change when the area is folded and opened again", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({ ...saved, bounds: BOUNDS });
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    useChatStore.getState().setLimit("maxToolRounds", 9);

    useChatStore.getState().setSettingsOpen(false);
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();

    // Re-opening reads what the core has, but reading over the draft would lose
    // what the reader typed without saying anything.
    expect(useChatStore.getState().limitsDraft?.maxToolRounds).toBe(9);
  });

  it("takes a fresh reading when there is nothing unsaved", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValueOnce({ ...saved, maxOpenPages: 4, bounds: BOUNDS });
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(useChatStore.getState().limitsDraft?.maxOpenPages).toBe(4);

    // Someone changed the project's file, or another window did.
    vi.mocked(fetchChatLimits).mockResolvedValueOnce({ ...saved, maxOpenPages: 2, bounds: BOUNDS });
    useChatStore.getState().setSettingsOpen(false);
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(useChatStore.getState().limitsDraft?.maxOpenPages).toBe(2);
  });

  it("reports why a save did not happen and keeps the draft", async () => {
    vi.mocked(fetchChatLimits).mockResolvedValue({ ...saved, bounds: BOUNDS });
    vi.mocked(putChatLimits).mockRejectedValue(new Error("historyLimit must be 1–200"));

    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    useChatStore.getState().setLimit("historyLimit", 5);
    await useChatStore.getState().saveLimits();

    const state = useChatStore.getState();
    expect(state.savingLimits).toBe(false);
    expect(state.limitsError).toBeTruthy();
    // The change is not lost, so it can be corrected and tried again.
    expect(state.limitsDraft?.historyLimit).toBe(5);
    expect(state.limits?.historyLimit).toBe(10);
  });

  it("says so when the limits cannot be read, and does not claim to show them", async () => {
    vi.mocked(fetchChatLimits).mockRejectedValue(new Error("读不到"));
    useChatStore.getState().setSettingsOpen(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(useChatStore.getState().limitsError).toBeTruthy();
  });
});

describe("asking a question", () => {
  it("shows the question and the answer before the core has them", async () => {
    vi.mocked(queryChat).mockImplementation(async (_project, _id, _content, options = {}) => {
      options.onStatus?.("正在思考", "think");
      options.onToken?.("答");
      // Mid-answer: both halves are on screen, and the conversation the project
      // holds is still empty. Text arriving also moves the turn to its last phase,
      // which the core would not say until it saves.
      expect(useChatStore.getState().pending).toEqual({
        question: "什么是熵",
        answer: "答",
        step: "answer",
      });
      expect(useChatStore.getState().open?.messages).toHaveLength(0);
      return conversation([turn("什么是熵"), turn("答案", "assistant")]);
    });

    await useChatStore.getState().ask("什么是熵");

    const state = useChatStore.getState();
    expect(state.pending).toBeNull();
    expect(state.open?.messages).toHaveLength(2);
    expect(state.conversations[0]).toMatchObject({ id: "c1", messageCount: 2 });
    expect(state.draft).toBe("");
    expect(state.turnError).toBeNull();
  });

  it("writes the answer in the order the core sends it", async () => {
    vi.mocked(queryChat).mockImplementation(async (_project, _id, _content, options = {}) => {
      options.onToken?.("信息");
      options.onToken?.("熵");
      options.onToken?.("测不准");
      expect(useChatStore.getState().pending?.answer).toBe("信息熵测不准");
      return conversation([]);
    });

    await useChatStore.getState().ask("什么是熵");
  });

  it("clears what a round wrote when that round turns out not to be the answer", async () => {
    vi.mocked(queryChat).mockImplementation(async (_project, _id, _content, options = {}) => {
      // A round that looked like prose, then asked to look something up.
      options.onToken?.("我先查一下");
      expect(useChatStore.getState().pending?.answer).toBe("我先查一下");
      options.onTokenReset?.();
      expect(useChatStore.getState().pending?.answer).toBe("");
      options.onToken?.("熵衡量不确定性");
      expect(useChatStore.getState().pending?.answer).toBe("熵衡量不确定性");
      return conversation([turn("什么是熵"), turn("熵衡量不确定性", "assistant")]);
    });

    await useChatStore.getState().ask("什么是熵");
    expect(useChatStore.getState().open?.messages[1]?.content).toBe("熵衡量不确定性");
  });

  it("refuses a second question while one is being answered", async () => {
    let release: (value: ChatConversation) => void = () => {};
    vi.mocked(queryChat).mockImplementation(
      () => new Promise<ChatConversation>((resolve) => (release = resolve)),
    );

    const first = useChatStore.getState().ask("第一问");
    await useChatStore.getState().ask("第二问");

    expect(queryChat).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().pending?.question).toBe("第一问");

    release(conversation([turn("第一问")]));
    await first;
  });

  it("clears the composer once the question is on its way", async () => {
    vi.mocked(queryChat).mockImplementation(async () => {
      expect(useChatStore.getState().draft).toBe("");
      return conversation([]);
    });
    await useChatStore.getState().ask("什么是熵");
  });
});

describe("when a turn fails", () => {
  it("keeps the project's own transcript and gives the question back", async () => {
    vi.mocked(queryChat).mockRejectedValue(new Error("这次没有答上来"));
    vi.mocked(fetchChat).mockResolvedValue(conversation());

    await useChatStore.getState().ask("什么是熵");

    const state = useChatStore.getState();
    expect(state.pending).toBeNull();
    expect(state.open?.messages).toHaveLength(0);
    // The failure's own wording is not a sentence for the reader, so the pane says
    // one written for them.
    expect(state.turnError).toBe(i18n.t("chat.askFailed"));
    // The question was never part of the conversation, so it is the reader's again.
    expect(state.draft).toBe("什么是熵");
  });

  it("does not hand back a question the core already kept", async () => {
    vi.mocked(queryChat).mockRejectedValue(new Error("断了"));
    vi.mocked(fetchChat).mockResolvedValue(conversation([turn("什么是熵")]));

    await useChatStore.getState().ask("什么是熵");

    const state = useChatStore.getState();
    expect(state.open?.messages).toHaveLength(1);
    // Asking it again would ask it twice.
    expect(state.draft).toBe("");
  });

  it("remembers the question, so it can be asked again", async () => {
    vi.mocked(queryChat).mockRejectedValue(new Error("断了"));
    vi.mocked(fetchChat).mockResolvedValue(conversation([turn("什么是熵")]));

    await useChatStore.getState().ask("什么是熵");

    // The draft is empty here (the core has the question), so this is the only way
    // back to it.
    expect(useChatStore.getState().failedQuestion).toBe("什么是熵");
  });

  it("asks it again when the reader retries, and lets the failure go", async () => {
    vi.mocked(queryChat).mockRejectedValueOnce(new Error("断了"));
    vi.mocked(fetchChat).mockResolvedValue(
      conversation([turn("什么是熵"), turn("答案", "assistant")]),
    );

    await useChatStore.getState().ask("什么是熵");
    expect(useChatStore.getState().failedQuestion).toBe("什么是熵");

    // The same question, so the core skips appending it a second time.
    vi.mocked(queryChat).mockResolvedValue(
      conversation([turn("什么是熵"), turn("答案", "assistant")]),
    );
    await useChatStore.getState().retry();

    const state = useChatStore.getState();
    expect(state.turnError).toBeNull();
    expect(state.failedQuestion).toBeNull();
    expect(vi.mocked(queryChat).mock.calls[1]?.[2]).toBe("什么是熵");
  });

  it("has nothing to retry when no turn failed", async () => {
    await useChatStore.getState().retry();
    expect(queryChat).not.toHaveBeenCalled();
  });

  it("does not offer to retry what the reader stopped", async () => {
    // Stopping is not a failure: nothing went wrong, and offering to try again
    // would say that it did.
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => reject(new Error("读者已停止")));
        }),
    );
    vi.mocked(fetchChat).mockResolvedValue(conversation([turn("什么是熵")]));

    const asking = useChatStore.getState().ask("什么是熵");
    useChatStore.getState().stop();
    await asking;

    const state = useChatStore.getState();
    expect(state.stopped).toBe(true);
    expect(state.failedQuestion).toBeNull();
  });

  it("keeps the question when the conversation cannot be read back either", async () => {
    vi.mocked(queryChat).mockRejectedValue(new Error("断了"));
    vi.mocked(fetchChat).mockRejectedValue(new Error("也读不到"));

    await useChatStore.getState().ask("什么是熵");

    expect(useChatStore.getState().draft).toBe("什么是熵");
    expect(useChatStore.getState().pending).toBeNull();
  });
});

describe("naming a conversation", () => {
  it("puts the typed name in the list and in the pane in front", async () => {
    vi.mocked(renameChat).mockResolvedValue({ ...conversation(), title: "熵的讨论" });

    await useChatStore.getState().rename("c1", "熵的讨论");

    expect(renameChat).toHaveBeenCalledWith("p", "c1", "熵的讨论");
    expect(useChatStore.getState().conversations[0].title).toBe("熵的讨论");
    // One title, not two: the pane is showing the same conversation.
    expect(useChatStore.getState().open?.title).toBe("熵的讨论");
    // What is in the pane is what was there; only its name changed.
    expect(useChatStore.getState().open?.messages).toEqual([]);
  });

  it("keeps a conversation it did not name itself", async () => {
    useChatStore.setState({
      conversations: [
        { id: "c1", title: "会话", updatedAt: "2026-09-19T00:00:00Z", messageCount: 2 },
        { id: "c2", title: "别的会话", updatedAt: "2026-09-18T00:00:00Z", messageCount: 1 },
      ],
    });
    vi.mocked(renameChat).mockResolvedValue({
      ...conversation(),
      id: "c2",
      title: "新名字",
    });

    await useChatStore.getState().rename("c2", "新名字");

    const [first, second] = useChatStore.getState().conversations;
    expect(first.title).toBe("会话");
    expect(second.title).toBe("新名字");
  });

  it("shows the automatic name again when the name is cleared", async () => {
    // The core decides what an unnamed conversation is called; the store takes its
    // answer rather than inventing a placeholder.
    vi.mocked(renameChat).mockResolvedValue({ ...conversation(), title: "什么是熵" });

    await useChatStore.getState().rename("c1", "");

    expect(renameChat).toHaveBeenCalledWith("p", "c1", "");
    expect(useChatStore.getState().conversations[0].title).toBe("什么是熵");
  });

  it("keeps the old name and says so when the rename did not happen", async () => {
    vi.mocked(renameChat).mockRejectedValue(new Error("写不进去"));

    await useChatStore.getState().rename("c1", "熵的讨论");

    const state = useChatStore.getState();
    expect(state.conversations[0].title).toBe("会话");
    expect(state.error).toBe(i18n.t("chat.renameFailed"));
  });
});

describe("deleting a conversation", () => {
  it("says it happened, and moves to the next conversation in front", async () => {
    vi.mocked(deleteChat).mockResolvedValue({
      limits: { ...LIMITS, bounds: BOUNDS },
      conversations: [
        { id: "c2", title: "另一个", updatedAt: "2026-09-18T00:00:00Z", messageCount: 1 },
      ],
    });
    vi.mocked(fetchChat).mockResolvedValue({ ...conversation(), id: "c2", title: "另一个" });

    const done = await useChatStore.getState().remove("c1");

    expect(done).toBe(true);
    expect(useChatStore.getState().currentId).toBe("c2");
    expect(useChatStore.getState().conversations.map((item) => item.id)).toEqual(["c2"]);
  });

  it("says it did not happen, so the ask stays open", async () => {
    vi.mocked(deleteChat).mockRejectedValue(new Error("删不掉"));

    const done = await useChatStore.getState().remove("c1");

    expect(done).toBe(false);
    expect(useChatStore.getState().conversations).toHaveLength(1);
    expect(useChatStore.getState().error).toBe(i18n.t("chat.deleteFailed"));
  });
});

describe("leaving a conversation", () => {
  it("drops the turn that was running in the conversation left behind", async () => {
    vi.mocked(queryChat).mockImplementation(
      (_project, _id, _content, options = {}) =>
        new Promise<ChatConversation>((_resolve, reject) => {
          options.onToken?.("答");
          options.signal?.addEventListener("abort", () =>
            reject(new Error("读者已停止")),
          );
        }),
    );
    vi.mocked(fetchChat).mockResolvedValue(conversation());

    const asking = useChatStore.getState().ask("什么是熵");
    await useChatStore.getState().openConversation("c2");
    await asking;

    const state = useChatStore.getState();
    expect(state.pending).toBeNull();
    expect(state.currentId).toBe("c2");
    // The abandoned turn has nowhere to belong, so it is not shown as a failure.
    expect(state.turnError).toBeNull();
  });
});
