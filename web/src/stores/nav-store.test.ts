import { beforeEach, describe, expect, it } from "vitest";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";

/**
 * Where a request from one view lands in the panes.
 *
 * A request is placed before it is published, because bringing the module on screen
 * is what puts its view in a position to answer at all. What is checked here is
 * that placement and where the reader was when they asked: the request has to say
 * itself which pane it came from, or the panes have to be read before they move —
 * reading them afterwards would answer with the destination.
 */
beforeEach(() => {
  useLayoutStore.setState({
    view: "docs",
    side: null,
    lastSide: "graph",
    focused: "main",
    paneReturn: {},
  });
  useNavStore.setState({ seq: 0, intent: null, plan: null, placement: "replace" });
});

const layout = () => useLayoutStore.getState();

describe("a page asked for beside what is being read", () => {
  it("lands next to the map it was picked from, not on top of it", () => {
    // Picking a page out of the link graph leaves the graph where it is: the map
    // the reader was working in is not the thing that moves.
    useLayoutStore.setState({ view: "graph", side: null, focused: "main" });

    const plan = useNavStore
      .getState()
      .go({ kind: "docs", path: "notes/a.md" }, { placement: "beside" });

    expect(plan.target).toBe("side");
    expect(layout().view).toBe("graph");
    expect(layout().side).toBe("docs");
    // The sidebar belongs to the pane in front, so the focus does not move off it.
    expect(layout().focused).toBe("main");
  });

  it("reads the reader's pane before the panes move", () => {
    // If the pane were read after the layout changed it would answer with where the
    // request landed, and the page being read would not come forward.
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "side" });

    const plan = useNavStore
      .getState()
      .go({ kind: "chat", conversationId: "c1" }, { placement: "beside" });

    expect(plan.promoteFrom).toBe("side");
    expect(plan.view).toBe("graph");
    expect(layout().view).toBe("graph");
    expect(layout().side).toBe("chat");
  });

  it("takes the pane a caller says it asked from", () => {
    // A view inside a pane knows which one it is in, and says so rather than leaving
    // it to be worked out from the focus.
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "main" });

    const plan = useNavStore
      .getState()
      .go({ kind: "docs", path: "notes/a.md" }, { placement: "beside", from: "side" });

    expect(plan.promoteFrom).toBe("side");
    expect(layout().view).toBe("graph");
    expect(layout().side).toBe("docs");
  });
});

describe("a thing asked for outright", () => {
  it("reads in the front pane and keeps a pane showing another module", () => {
    useLayoutStore.setState({ view: "graph", side: "sources", focused: "side" });

    const plan = useNavStore
      .getState()
      .go({ kind: "docs", path: "notes/a.md" }, { placement: "replace" });

    expect(plan.target).toBe("main");
    expect(layout().view).toBe("docs");
    expect(layout().side).toBe("sources");
  });

  it("keeps a pane that was showing the same module", () => {
    // Two pages side by side is what a split is for, and the reader is only saying
    // which of them they are reading now.
    useLayoutStore.setState({ view: "docs", side: "docs", focused: "side" });

    useNavStore.getState().go({ kind: "docs", path: "notes/a.md" }, { placement: "replace" });

    expect(layout().side).toBe("docs");
    expect(layout().focused).toBe("main");
  });
});

describe("a thing asked for in place", () => {
  it("takes the reader's own pane and remembers what it displaced", () => {
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "side" });

    const plan = useNavStore
      .getState()
      .go({ kind: "browse", url: "https://example.com/" }, { placement: "in-place" });

    expect(plan.target).toBe("side");
    expect(layout().side).toBe("browse");
    expect(layout().paneReturn.side).toBe("graph");
  });
});

describe("a thing asked for without publishing", () => {
  it("places the panes but leaves the last intent alone", () => {
    // A view already inside a pane answers its own request; publishing it would ask
    // that view to answer a second time.
    useLayoutStore.setState({ view: "docs", side: null, focused: "main" });
    const before = useNavStore.getState().seq;

    const plan = useNavStore.getState().place({
      module: "docs",
      placement: "replace",
      from: "main",
    });

    expect(plan.target).toBe("main");
    expect(layout().view).toBe("docs");
    expect(useNavStore.getState().seq).toBe(before);
    expect(useNavStore.getState().plan).toBeNull();
  });
});

describe("what a request publishes", () => {
  it("says where it landed, for the view that has to fill the pane", () => {
    useLayoutStore.setState({ view: "graph", side: null, focused: "main" });
    const before = useNavStore.getState().seq;

    const plan = useNavStore
      .getState()
      .go({ kind: "docs", path: "notes/a.md", locator: "b2" }, { placement: "beside" });

    const state = useNavStore.getState();
    expect(state.seq).toBe(before + 1);
    expect(state.intent).toEqual({ kind: "docs", path: "notes/a.md", locator: "b2" });
    // The answer is kept, not only returned: the view answering it runs afterwards.
    expect(state.plan).toBe(plan);
    expect(state.placement).toBe("beside");
  });
});
