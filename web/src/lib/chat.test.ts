import { describe, expect, it } from "vitest";
import { matchesConversation, questionBefore } from "@/lib/chat";
import type { SessionTurn } from "@/lib/api/types";

function turn(
  content: string,
  role: SessionTurn["role"] = "user",
): Pick<SessionTurn, "role" | "content"> {
  return { role, content };
}

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
