import { describe, expect, it } from "vitest";
import { findInlineMarks, outerMarks, type InlineMark } from "@/lib/inline-marks";

/**
 * The marks a page's own prose can carry beyond markdown.
 *
 * What is asked here is what the rule finds and where its delimiters and words
 * stand, which is the whole of the rule: what a page draws from it follows. The
 * same answer is used by the editor and by the reading view, so a mark that is
 * found here is one both of them will show.
 */

/** Each mark as the text it covers, its kind, and the words inside it. */
function marks(text: string) {
  return findInlineMarks(text).map((mark: InlineMark) => ({
    literal: text.slice(mark.from, mark.to),
    kind: mark.kind,
    words: text.slice(mark.innerFrom, mark.innerTo),
  }));
}

describe("a highlight", () => {
  it("is two pairs of equals around its words", () => {
    expect(marks("a ==marked== word")).toEqual([
      { literal: "==marked==", kind: "highlight", words: "marked" },
    ]);
  });

  it("keeps its words as written, spaces inside them included", () => {
    expect(marks("==two words==\n")).toEqual([
      { literal: "==two words==", kind: "highlight", words: "two words" },
    ]);
  });

  it("is not a highlight when the pair is not written", () => {
    expect(marks("a == b\n")).toEqual([]);
    expect(marks("a == b ==\n")).toEqual([]);
    expect(marks("a ==marked=\n")).toEqual([]);
  });

  it("is not a highlight when an equals stands among the words", () => {
    // The pair would be ambiguous: `==a=b==` could as well be read from the
    // second equals outward.
    expect(marks("==a=b==\n")).toEqual([]);
  });
});

describe("an underline", () => {
  it("stands between its two tags", () => {
    expect(marks("a <u>under</u> word")).toEqual([
      { literal: "<u>under</u>", kind: "underline", words: "under" },
    ]);
  });

  it("needs both tags", () => {
    expect(marks("a <u>under word\n")).toEqual([]);
    expect(marks("a under</u> word\n")).toEqual([]);
  });

  it("needs words between them", () => {
    expect(marks("a <u></u> word\n")).toEqual([]);
  });
});

describe("a raised or lowered mark", () => {
  it("is one character used twice", () => {
    expect(marks("x^2^\n")).toEqual([{ literal: "^2^", kind: "sup", words: "2" }]);
    expect(marks("H~2~O\n")).toEqual([{ literal: "~2~", kind: "sub", words: "2" }]);
  });

  it("holds no space, as the syntax tree's own reader holds none", () => {
    expect(marks("^a b^\n")).toEqual([]);
    expect(marks("~a b~\n")).toEqual([]);
  });

  it("takes a backslash to make the character after it ordinary", () => {
    expect(marks("^a\\ b^\n")).toEqual([{ literal: "^a\\ b^", kind: "sup", words: "a\\ b" }]);
  });

  it("leaves a doubled delimiter to the mark markdown knows", () => {
    expect(marks("a ~~cut~~ word\n").map((mark) => mark.kind)).toEqual([]);
  });
});

describe("several marks on one page", () => {
  it("are found in the order they stand", () => {
    expect(marks("==a== then <u>b</u> then ^c^\n").map((mark) => mark.kind)).toEqual([
      "highlight",
      "underline",
      "sup",
    ]);
  });

  it("are not read inside code", () => {
    expect(marks("`==a==`\n")).toEqual([]);
    expect(marks("```\n==a==\n```\n")).toEqual([]);
    expect(marks("a ``x ==y== z``\n")).toEqual([]);
  });

  it("reads the marks a mark's own words hold", () => {
    expect(marks("==H~2~O==\n")).toEqual([
      { literal: "==H~2~O==", kind: "highlight", words: "H~2~O" },
      { literal: "~2~", kind: "sub", words: "2" },
    ]);
  });

  it("knows which of them are outermost", () => {
    const text = "==H~2~O== and ~x~\n";
    expect(outerMarks(findInlineMarks(text)).map((mark) => mark.kind)).toEqual([
      "highlight",
      "sub",
    ]);
  });

  it("does not read the words of one mark for another's delimiter", () => {
    // `^` inside a highlight's words is raised, and the `==` around them still
    // closes where it says it does.
    expect(marks("==a ^b^ c==\n").map((mark) => mark.kind)).toEqual(["highlight", "sup"]);
  });
});
