import { describe, expect, it } from "vitest";
import {
  canGoBack,
  canGoForward,
  goBack,
  goForward,
  HISTORY_DEPTH,
  record,
  samePair,
  startHistory,
  view,
  type ReadingPair,
} from "@/lib/reading-history";

const note = (path: string): ReadingPair => ({ main: view("docs", path), side: null });

const noteWithSource = (path: string, source: string): ReadingPair => ({
  main: view("docs", path),
  side: view("docs", source),
});

describe("a reading pair", () => {
  it("compares the two panes and their targets", () => {
    expect(samePair(noteWithSource("a", "b"), noteWithSource("a", "b"))).toBe(true);
    expect(samePair(noteWithSource("a", "b"), noteWithSource("a", "c"))).toBe(false);
    expect(samePair(note("a"), noteWithSource("a", "b"))).toBe(false);
  });

  it("tells a page seen two ways apart, since the module is part of it", () => {
    const inDocs = { main: view("docs", "raw/web/a.html"), side: null };
    const inBrowser = { main: view("browse", "https://a.example/"), side: null };
    expect(samePair(inDocs, inBrowser)).toBe(false);
  });

  it("needs the side pane to be absent on both sides to call a pair the same", () => {
    expect(samePair(note("a"), note("a"))).toBe(true);
    expect(samePair({ main: view("docs", "a"), side: null }, { main: view("docs", "a"), side: null })).toBe(
      true,
    );
  });
});

describe("taking steps", () => {
  it("remembers where it was when the reader moves on", () => {
    const history = record(record(startHistory(note("a")), note("b")), note("c"));
    expect(history.current.main.target).toBe("c");
    expect(history.past.map((pair) => pair.main.target)).toEqual(["a", "b"]);
    expect(canGoBack(history)).toBe(true);
  });

  it("does not count looking at the same place twice", () => {
    // Re-opening what is in front, or switching between two tabs of one page, is
    // not a place the reader moved to.
    const once = record(startHistory(note("a")), note("b"));
    expect(record(once, note("b"))).toBe(once);
  });

  it("keeps a whole pair, not just the page in front", () => {
    const history = record(startHistory(noteWithSource("note", "source")), note("other"));
    expect(history.past[0]?.side?.target).toBe("source");
  });

  it("drops what it had gone back from once the reader moves on", () => {
    const three = record(record(startHistory(note("a")), note("b")), note("c"));
    const back = goBack(three);
    expect(canGoForward(back)).toBe(true);
    const moved = record(back, note("d"));
    expect(canGoForward(moved)).toBe(false);
    expect(moved.past.map((pair) => pair.main.target)).toEqual(["a", "b"]);
  });

  it("keeps at most the depth it is allowed, dropping the oldest", () => {
    let history = startHistory(note("p0"));
    for (let step = 1; step <= HISTORY_DEPTH + 5; step += 1) {
      history = record(history, note(`p${step}`));
    }
    expect(history.past).toHaveLength(HISTORY_DEPTH);
    // The first steps are the ones that fell away.
    expect(history.past[0]?.main.target).toBe("p5");
  });
});

describe("going back and forward", () => {
  it("returns to the pair before this one, both panes together", () => {
    const history = record(startHistory(noteWithSource("note", "source")), note("other"));
    const back = goBack(history);
    expect(back.current.main.target).toBe("note");
    expect(back.current.side?.target).toBe("source");
    expect(canGoForward(back)).toBe(true);
  });

  it("comes forward to the same pair again", () => {
    const history = record(startHistory(noteWithSource("note", "source")), note("other"));
    const there = goForward(goBack(history));
    expect(samePair(there.current, history.current)).toBe(true);
    expect(there.future).toHaveLength(0);
  });

  it("walks more than one step in either direction", () => {
    const three = record(record(startHistory(note("a")), note("b")), note("c"));
    const first = goBack(goBack(three));
    expect(first.current.main.target).toBe("a");
    expect(canGoBack(first)).toBe(false);
    const last = goForward(goForward(first));
    expect(last.current.main.target).toBe("c");
    expect(canGoForward(last)).toBe(false);
  });

  it("stays put at either end", () => {
    const history = startHistory(note("a"));
    expect(goBack(history)).toBe(history);
    expect(goForward(history)).toBe(history);
    expect(canGoBack(history)).toBe(false);
    expect(canGoForward(history)).toBe(false);
  });

  it("keeps the steps it walked away from when it walks back and forth", () => {
    const three = record(record(startHistory(note("a")), note("b")), note("c"));
    // Back one and forward one leaves the same history as before.
    const round = goForward(goBack(three));
    expect(round.past.map((pair) => pair.main.target)).toEqual(["a", "b"]);
    expect(round.current.main.target).toBe("c");
  });
});
