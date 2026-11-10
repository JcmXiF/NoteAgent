import { beforeEach, describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import {
  rearmOnBusy,
  useWaitContextStore,
} from "@/stores/wait-context-store";

beforeEach(() => {
  useWaitContextStore.setState({ pending: null });
});

describe("the wait a job button is about to start", () => {
  it("hands a plan wait to the plan action and spends it", () => {
    const context = {
      sessionId: "s1",
      waitId: "w1",
      job: "plan",
      interface: "source",
    };

    useWaitContextStore.getState().arm(context);

    expect(useWaitContextStore.getState().take("plan")).toEqual(context);
    expect(useWaitContextStore.getState().pending).toBeNull();
  });

  it("does not spend a compile wait on a plan press", () => {
    const context = {
      sessionId: "s1",
      waitId: "w1",
      job: "compile",
      interface: "plan_board",
    };
    useWaitContextStore.getState().arm(context);

    expect(useWaitContextStore.getState().take("plan")).toBeNull();
    expect(useWaitContextStore.getState().pending).toEqual(context);
  });

  it("puts the wait back when another job made the press busy", () => {
    const context = {
      sessionId: "s1",
      waitId: "w1",
      job: "compile",
      interface: "plan_board",
    };
    useWaitContextStore.getState().arm(context);
    const taken = useWaitContextStore.getState().take("compile");

    rearmOnBusy(new ApiError("busy", 409, "job.busy"), taken);

    expect(useWaitContextStore.getState().pending).toEqual(context);
  });

  it("does not put the wait back after another failure", () => {
    const context = {
      sessionId: "s1",
      waitId: "w1",
      job: "compile",
      interface: "plan_board",
    };
    useWaitContextStore.getState().arm(context);
    const taken = useWaitContextStore.getState().take("compile");

    rearmOnBusy(new Error("the stream broke"), taken);

    expect(useWaitContextStore.getState().pending).toBeNull();
  });

  it("can be dropped when the reader changes their mind", () => {
    useWaitContextStore.getState().arm({
      sessionId: "s1",
      waitId: "w1",
      job: "plan",
      interface: "source",
    });

    useWaitContextStore.getState().clear();

    expect(useWaitContextStore.getState().pending).toBeNull();
  });
});
