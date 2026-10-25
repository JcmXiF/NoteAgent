// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { PaneFrame } from "@/components/layout/PaneFrame";
import { startHistory, view, type ReadingHistory } from "@/lib/reading-history";
import type { PaneSlot } from "@/stores/layout-store";
import { useReadingStore } from "@/stores/reading-store";

// The frame is what is being checked here: which module is drawn in a pane, and what
// that module brings with it, is another test's business.
vi.mock("@/components/module-views", () => ({
  moduleViews: () => ({ Sidebar: () => null, Content: () => null }),
}));

/**
 * The steps back and forward sit in the pane that always exists.
 *
 * A step is a place both panes were in together, so the way back is the window's and
 * not a module's: it has to be reachable while any module is in front, and while
 * nothing is open at all. Whether there is anything to show is read from the steps
 * rather than decided per module.
 *
 * Drawn into a document rather than rendered to a string: a string render answers
 * from the store's first state, so a history arranged for the test would not be the
 * history on screen.
 */

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
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

const history = (
  past: ReadingHistory["past"],
  current: ReadingHistory["current"],
): ReadingHistory => ({ past, current, future: [] });

function draw(slot: PaneSlot): HTMLElement {
  act(() => {
    root.render(<PaneFrame slot={slot} module="graph" focused={false} />);
  });
  return host;
}

/** A control in the header, found the way a reader's assistive tool names it. */
const control = (slot: PaneSlot, key: string): HTMLButtonElement | null =>
  draw(slot).querySelector<HTMLButtonElement>(`header button[aria-label="${i18n.t(key)}"]`);

describe("the steps in a pane's header", () => {
  it("are not there when there is nowhere to have come from", () => {
    useReadingStore.setState({ history: startHistory({ main: view("graph"), side: null }) });

    expect(control("main", "nav.back")).toBeNull();
    expect(control("main", "nav.forward")).toBeNull();
  });

  it("appear once a step has been taken, whatever the pane is showing", () => {
    useReadingStore.setState({
      history: history([{ main: view("docs", "wiki/concepts/熵.md"), side: null }], {
        main: view("graph"),
        side: null,
      }),
    });

    // Somewhere behind them, and nothing ahead of them yet.
    expect(control("main", "nav.back")?.disabled).toBe(false);
    expect(control("main", "nav.forward")?.disabled).toBe(true);
  });

  it("stay in the pane that is always there, not in the one opened beside it", () => {
    useReadingStore.setState({
      history: history([{ main: view("docs", "wiki/concepts/熵.md"), side: null }], {
        main: view("docs", "wiki/concepts/熵.md"),
        side: view("graph"),
      }),
    });

    expect(control("side", "nav.back")).toBeNull();
    expect(control("main", "nav.back")).not.toBeNull();
  });
});
