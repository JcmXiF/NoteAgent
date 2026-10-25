import { describe, expect, it } from "vitest";
import {
  CHAT_LIMIT_FIELDS,
  CHAT_LIMIT_GROUPS,
  limitValues,
  outOfRange,
  sameLimits,
  valueFromInput,
} from "@/lib/chat-limits";
import type { ChatLimitBounds, ChatLimits } from "@/lib/api/types";

function limits(patch: Partial<ChatLimits> = {}): ChatLimits {
  const base: ChatLimits = {
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
  return { ...base, ...patch };
}

/** Ranges as the core reports them, for the bones of the check. */
function bounds(overrides: Partial<ChatLimitBounds> = {}): ChatLimitBounds {
  const base = {} as ChatLimitBounds;
  for (const field of CHAT_LIMIT_FIELDS) {
    // Wide enough to accept the values above, so a test only fails on the field it
    // means to test.
    base[field.key] = { min: 0, max: 1_000_000 };
  }
  return { ...base, ...overrides };
}

describe("the knob list", () => {
  it("covers every knob exactly once", () => {
    // The panel renders this list, so a knob missing from it is a knob nobody can
    // change and a knob listed twice is two inputs fighting over one value.
    const listed = CHAT_LIMIT_FIELDS.map((field) => field.key);
    expect(new Set(listed).size).toBe(listed.length);
    expect(new Set(listed)).toEqual(new Set(Object.keys(limits())));
  });

  it("groups the knobs and keeps the order it declares", () => {
    expect(CHAT_LIMIT_GROUPS.map((group) => group.fields.length)).toEqual([5, 2, 4]);
    const listed = CHAT_LIMIT_FIELDS.map((field) => field.key);
    expect(listed[0]).toBe("historyLimit");
    expect(listed[listed.length - 1]).toBe("maxToolRounds");
  });

  it("gives every field a name and a unit", () => {
    for (const field of CHAT_LIMIT_FIELDS) {
      expect(field.label).toMatch(/^chat\.limits\./);
      expect(field.unit).toMatch(/^chat\.limits\.unit\./);
    }
  });

  it("carries no ranges of its own", () => {
    // The core decides what a knob accepts and sends it with the values; a second
    // copy here would be a copy that drifts.
    for (const field of CHAT_LIMIT_FIELDS) {
      expect(field).not.toHaveProperty("min");
      expect(field).not.toHaveProperty("max");
    }
  });
});

describe("sameLimits", () => {
  it("is true for the same values", () => {
    expect(sameLimits(limits(), limits())).toBe(true);
  });

  it("notices a change in any knob, without being told which", () => {
    for (const field of CHAT_LIMIT_FIELDS) {
      const changed = limits({ [field.key]: limits()[field.key] + 1 });
      expect(sameLimits(limits(), changed)).toBe(false);
    }
  });

  it("is false when only one side is known", () => {
    expect(sameLimits(null, limits())).toBe(false);
    expect(sameLimits(limits(), null)).toBe(false);
    expect(sameLimits(null, null)).toBe(true);
  });
});

describe("limitValues", () => {
  it("keeps the values and drops the ranges", () => {
    const payload = { ...limits(), bounds: bounds() };
    const values = limitValues(payload);
    expect(values).toEqual(limits());
    expect(values).not.toHaveProperty("bounds");
  });

  it("is nothing for nothing", () => {
    expect(limitValues(null)).toBe(null);
    expect(limitValues(undefined)).toBe(null);
  });

  it("makes a payload compare equal to its own values", () => {
    // A set that still carried the ranges would compare unequal to itself, since the
    // two ranges are different objects — and the panel would look permanently unsaved.
    const payload = { ...limits(), bounds: bounds() };
    expect(sameLimits(limits(), limitValues(payload))).toBe(true);
  });
});

describe("outOfRange", () => {
  it("names the fields outside the ranges the core sent", () => {
    const spans = bounds({ historyLimit: { min: 1, max: 200 } });
    expect(outOfRange(limits({ historyLimit: 201 }), spans)).toEqual(["historyLimit"]);
    expect(outOfRange(limits({ historyLimit: 0 }), spans)).toEqual(["historyLimit"]);
    expect(outOfRange(limits({ historyLimit: 200 }), spans)).toEqual([]);
    expect(outOfRange(limits({ historyLimit: 1 }), spans)).toEqual([]);
  });

  it("treats a cleared field as something to fix", () => {
    const spans = bounds({ maxOpenPages: { min: 1, max: 30 } });
    expect(outOfRange(limits({ maxOpenPages: valueFromInput("") }), spans)).toEqual([
      "maxOpenPages",
    ]);
    expect(outOfRange(limits({ maxOpenPages: valueFromInput("abc") }), spans)).toEqual([
      "maxOpenPages",
    ]);
  });

  it("keeps a whole number that was typed with decimals", () => {
    expect(valueFromInput("12.7")).toBe(12);
    expect(valueFromInput(" 40 ")).toBe(40);
  });

  it("calls nothing wrong when the ranges are not known", () => {
    // Saying a value is wrong without knowing the range would be worse than letting
    // the core be the one to answer.
    expect(outOfRange(limits({ historyLimit: 99_999 }), null)).toEqual([]);
    expect(outOfRange(limits({ historyLimit: 99_999 }), undefined)).toEqual([]);
  });

  it("ignores a knob the core did not send a range for", () => {
    const spans = bounds();
    delete (spans as Partial<ChatLimitBounds>).maxToolRounds;
    expect(outOfRange(limits({ maxToolRounds: 99_999 }), spans)).toEqual([]);
  });
});
