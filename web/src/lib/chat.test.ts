import { describe, expect, it } from "vitest";
import {
  canSend,
  activityKey,
  furthestStep,
  isAtEnd,
  matchesConversation,
  questionBefore,
  questionIsUnsent,
  spokenStep,
  type PendingTurn,
} from "@/lib/chat";
import type { ChatTurn } from "@/lib/api/types";

function turn(content: string, role: ChatTurn["role"] = "user"): ChatTurn {
  return { id: content, role, content, createdAt: "2026-09-19T00:00:00Z" };
}

describe("canSend", () => {
  it("needs something to send", () => {
    expect(canSend("", 100, false)).toBe(false);
    expect(canSend("   \n ", 100, false)).toBe(false);
  });

  it("counts only what would actually be sent", () => {
    // The padding is trimmed before the core sees the question, so it would be
    // wrong to refuse it for being long.
    expect(canSend(`  ${"x".repeat(100)}  `, 100, false)).toBe(true);
  });

  it("allows a question exactly at the limit", () => {
    expect(canSend("x".repeat(100), 100, false)).toBe(true);
    expect(canSend("x".repeat(101), 100, false)).toBe(false);
  });

  it("refuses a second question while one is being answered", () => {
    expect(canSend("hello", 100, true)).toBe(false);
  });

  it("does not judge the length when no limit is known", () => {
    // The core sends the limit it enforces; until it has, this side has nothing to
    // measure against, and inventing one would be a number that can drift.
    expect(canSend("x".repeat(100_000), null, false)).toBe(true);
  });
});

describe("isAtEnd", () => {
  it("is true at the end and within the margin", () => {
    expect(isAtEnd({ scrollTop: 400, scrollHeight: 1000, clientHeight: 600 })).toBe(true);
    expect(isAtEnd({ scrollTop: 380, scrollHeight: 1000, clientHeight: 600 })).toBe(true);
  });

  it("is false once the reader has scrolled up", () => {
    expect(isAtEnd({ scrollTop: 300, scrollHeight: 1000, clientHeight: 600 })).toBe(false);
    expect(isAtEnd({ scrollTop: 0, scrollHeight: 1000, clientHeight: 600 })).toBe(false);
  });

  it("is true when the content is shorter than the view", () => {
    expect(isAtEnd({ scrollTop: 0, scrollHeight: 120, clientHeight: 600 })).toBe(true);
  });

  it("is true when the measurements are not usable", () => {
    // A pane measured at zero before layout should not read as scrolled away.
    expect(isAtEnd({ scrollTop: 0, scrollHeight: 0, clientHeight: 0 })).toBe(true);
  });
});

describe("activityKey", () => {
  it("speaks for the step the core named", () => {
    expect(activityKey("tool")).toBe("chat.activity.tool");
    expect(activityKey("answer")).toBe("chat.activity.answer");
    expect(activityKey("think")).toBe("chat.activity.think");
  });

  it("reads a step it does not know as still thinking", () => {
    // Anything before an answer is written is, in fact, still being thought about.
    expect(activityKey(null)).toBe("chat.activity.think");
    expect(activityKey("something-new")).toBe("chat.activity.think");
  });
});

describe("furthestStep", () => {
  it("moves on to the phase the core names", () => {
    expect(furthestStep(null, "think")).toBe("think");
    expect(furthestStep("think", "tool")).toBe("tool");
    expect(furthestStep("tool", "answer")).toBe("answer");
  });

  it("does not go back when a turn thinks again after a tool", () => {
    // The core reports `think` after every tool call; a track that followed that
    // literally would appear to rewind.
    expect(furthestStep("tool", "think")).toBe("tool");
    expect(furthestStep("answer", "tool")).toBe("answer");
    expect(furthestStep("answer", "think")).toBe("answer");
  });

  it("keeps what it has when the core says nothing useful", () => {
    expect(furthestStep("tool", null)).toBe("tool");
    expect(furthestStep("tool", "")).toBe("tool");
    // A phase this side has never heard of must not jump the track to the end.
    expect(furthestStep("think", "something-new")).toBe("think");
    expect(furthestStep(null, "something-new")).toBe(null);
  });
});

describe("spokenStep", () => {
  it("says the answer is being written once there is text", () => {
    // The core only names the answering phase when it saves, which is after the
    // answer has been written, so text arriving is the earlier and truer signal.
    expect(spokenStep("信息", "tool")).toBe("answer");
    expect(spokenStep("信息", "think")).toBe("answer");
    expect(spokenStep("信息", null)).toBe("answer");
  });

  it("keeps the phase the core named while nothing is being written", () => {
    expect(spokenStep("", "tool")).toBe("tool");
    expect(spokenStep("   ", "think")).toBe("think");
    expect(spokenStep("", null)).toBe(null);
  });
});

describe("questionIsUnsent", () => {
  const pending: PendingTurn = {
    question: "什么是熵",
    answer: "",
    step: null,
  };

  it("is false when the core already has the question", () => {
    expect(questionIsUnsent([turn("你好"), turn("什么是熵")], "什么是熵")).toBe(false);
  });

  it("ignores surrounding space when comparing", () => {
    expect(questionIsUnsent([turn("  什么是熵  ")], pending.question)).toBe(false);
  });

  it("is true when the last turn is the answer to something else", () => {
    expect(questionIsUnsent([turn("什么是熵"), turn("熵是…", "assistant")], "另一个问题")).toBe(
      true,
    );
  });

  it("is true in an empty conversation", () => {
    expect(questionIsUnsent([], "什么是熵")).toBe(true);
  });

  it("is true when a different question was saved last", () => {
    expect(questionIsUnsent([turn("先问这个")], "再问那个")).toBe(true);
  });
});

describe("questionBefore", () => {
  it("is the question an answer answers", () => {
    const messages = [turn("什么是熵"), turn("熵是……", "assistant")];
    expect(questionBefore(messages, 1)).toBe("什么是熵");
  });

  it("reads past an answer with no question of its own", () => {
    // A turn that failed leaves its question, then the next answer follows it.
    const messages = [turn("什么是熵"), turn("答到一半", "assistant"), turn("再说一次", "assistant")];
    expect(questionBefore(messages, 2)).toBe("什么是熵");
  });

  it("is null before anything was asked", () => {
    expect(questionBefore([turn("熵是……", "assistant")], 0)).toBe(null);
    expect(questionBefore([], 0)).toBe(null);
  });
});

describe("matchesConversation", () => {
  const untitled = "新会话";

  it("finds a conversation by part of its name, in any case", () => {
    expect(matchesConversation("关于熵的讨论", untitled, "熵")).toBe(true);
    expect(matchesConversation("Entropy and KL", untitled, "kl")).toBe(true);
    expect(matchesConversation("Entropy and KL", untitled, "KL")).toBe(true);
  });

  it("needs every word that was typed, in any order", () => {
    expect(matchesConversation("关于熵的讨论", untitled, "讨论 熵")).toBe(true);
    expect(matchesConversation("关于熵的讨论", untitled, "熵 互信息")).toBe(false);
  });

  it("searches an unnamed conversation by the name it is shown under", () => {
    // What is on screen is what is searched: an unnamed conversation is not invisible.
    expect(matchesConversation("", untitled, "新会话")).toBe(true);
    expect(matchesConversation("", untitled, "熵")).toBe(false);
  });

  it("finds everything when nothing was typed", () => {
    expect(matchesConversation("关于熵的讨论", untitled, "")).toBe(true);
    expect(matchesConversation("", untitled, "   ")).toBe(true);
  });
});
