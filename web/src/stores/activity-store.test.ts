import { beforeEach, describe, expect, it } from "vitest";
import { useActivityStore, type ActivityStep } from "@/stores/activity-store";

/**
 * The step track is a progress report, not a transcript of what was called: it must
 * be safe for a job that revisits a phase (a chat turn thinks again after every tool
 * call) as well as for one that only moves forward.
 */
function track(): ActivityStep[] {
  return [
    { id: "think", label: "Think", state: "current" },
    { id: "tool", label: "Look", state: "pending" },
    { id: "answer", label: "Answer", state: "pending" },
  ];
}

const states = (id: string) =>
  (useActivityStore.getState().items.find((item) => item.id === id)?.steps ?? []).map(
    (step) => step.state,
  );

beforeEach(() => {
  useActivityStore.setState({ items: [] });
});

describe("the step track", () => {
  it("advances to the phase the job names", () => {
    const id = useActivityStore.getState().start({
      kind: "chat",
      title: "t",
      steps: track(),
    });
    useActivityStore.getState().setStatus(id, "looking", "tool");
    expect(states(id)).toEqual(["done", "current", "pending"]);
    useActivityStore.getState().setStatus(id, "answering", "answer");
    expect(states(id)).toEqual(["done", "done", "current"]);
  });

  it("does not move back when a job revisits an earlier phase", () => {
    const id = useActivityStore.getState().start({
      kind: "chat",
      title: "t",
      steps: track(),
    });
    useActivityStore.getState().setStatus(id, "thinking", "think");
    useActivityStore.getState().setStatus(id, "looking", "tool");
    // Back to thinking after the tool returned; the track stays where it was.
    useActivityStore.getState().setStatus(id, "thinking again", "think");
    expect(states(id)).toEqual(["done", "current", "pending"]);
  });

  it("follows the labels of the steps it is given", () => {
    const id = useActivityStore.getState().start({
      kind: "chat",
      title: "t",
      steps: track(),
    });
    useActivityStore.getState().setStatus(id, "x", "tool");
    const labels = useActivityStore
      .getState()
      .items[0].steps?.map((step) => step.label);
    expect(labels).toEqual(["Think", "Look", "Answer"]);
  });

  it("ignores a phase it does not know", () => {
    const id = useActivityStore.getState().start({
      kind: "chat",
      title: "t",
      steps: track(),
    });
    useActivityStore.getState().setStatus(id, "?", "something-new");
    expect(states(id)).toEqual(["current", "pending", "pending"]);
  });

  it("marks every step done when the job finishes", () => {
    const id = useActivityStore.getState().start({
      kind: "chat",
      title: "t",
      steps: track(),
    });
    useActivityStore.getState().setStatus(id, "looking", "tool");
    useActivityStore.getState().finish(id, {});
    expect(states(id)).toEqual(["done", "done", "done"]);
  });
});
