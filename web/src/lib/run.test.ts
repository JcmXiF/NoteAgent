import { beforeEach, describe, expect, it } from "vitest";
import { runAsActivity } from "@/lib/run";
import { useActivityStore } from "@/stores/activity-store";
import i18n from "@/i18n";

function items() {
  return useActivityStore.getState().items;
}

beforeEach(() => {
  useActivityStore.setState({ items: [] });
});

describe("runAsActivity", () => {
  it("registers the job, returns the result and marks it finished", async () => {
    const result = await runAsActivity({
      kind: "plan",
      title: "Plan the pages",
      module: "docs",
      run: async () => 7,
      filesWritten: (value) => [`page-${value}.md`],
    });

    expect(result).toBe(7);
    const [item] = items();
    expect(item.kind).toBe("plan");
    expect(item.title).toBe("Plan the pages");
    expect(item.module).toBe("docs");
    expect(item.phase).toBe("done");
    expect(item.filesWritten).toEqual(["page-7.md"]);
    expect(item.cancel).toBeUndefined();
  });

  it("reports progress and log lines from inside the job", async () => {
    let duringRun: string[] = [];
    await runAsActivity({
      kind: "fetch",
      title: "https://example.com",
      steps: [
        { id: "read", label: "Read", state: "pending" },
        { id: "extract", label: "Extract", state: "pending" },
      ],
      run: async (ctx) => {
        ctx.status("Fetching the page…");
        ctx.note("urls=1");
        ctx.status("Reading it…", "read");
        // Read mid-flight: finishing marks every step done by design.
        duringRun = items()[0].steps?.map((step) => step.state) ?? [];
      },
    });

    const [item] = items();
    expect(item.detail).toEqual(["urls=1"]);
    expect(item.currentStepId).toBe("read");
    expect(duringRun).toEqual(["current", "pending"]);
    // A finished job shows the whole track as done.
    expect(item.steps?.map((step) => step.state)).toEqual(["done", "done"]);
  });

  it("drops the progress line once the job ends, so it cannot claim to be running", async () => {
    await runAsActivity({
      kind: "fetch",
      title: "https://example.com",
      run: async (ctx) => {
        ctx.status("Reading it…");
      },
    });
    expect(items()[0].phase).toBe("done");
    expect(items()[0].message).toBeUndefined();
  });

  it("shows the outcome instead when the job provides one", async () => {
    await runAsActivity({
      kind: "fetch",
      title: "https://example.com",
      run: async (ctx) => {
        ctx.status("Reading it…");
        return 2;
      },
      doneMessage: (count) => `Added ${count}`,
    });
    expect(items()[0].message).toBe("Added 2");
  });

  it("keeps a failure's own wording in the job's log, and words the panel itself", async () => {
    await expect(
      runAsActivity({
        kind: "fetch",
        title: "https://example.com",
        run: async () => {
          throw new Error("The site refused the request");
        },
      }),
    ).rejects.toThrow("The site refused the request");

    const [item] = items();
    expect(item.phase).toBe("error");
    // Wording written for whoever reads a log has no place on a screen: this job's
    // log keeps what was said, and the panel says something written for the reader.
    expect(item.detail.join("\n")).toContain("The site refused the request");
    expect(item.error).toBe(i18n.t("activity.failed"));
    // A failed job keeps a retry action so it can be tried from the panel.
    expect(item.cancel).toBeUndefined();
  });

  it("falls back to a generic message when the failure is not an Error", async () => {
    await expect(
      runAsActivity({
        kind: "misc",
        title: "Something",
        run: async () => {
          throw "just a string";
        },
      }),
    ).rejects.toBe("just a string");

    expect(items()[0].phase).toBe("error");
    expect(items()[0].error).toBeTruthy();
  });

  it("marks a job cancelled, not failed, when the caller stops it", async () => {
    const pending = runAsActivity({
      kind: "fetch",
      title: "https://example.com",
      run: async (ctx) => {
        // Subscribe before stopping: an abort event does not replay for later
        // listeners. Then stop it the way the panel does.
        const stopped = new Promise<never>((_, reject) => {
          ctx.signal.addEventListener("abort", () => reject(new Error("stopped")), {
            once: true,
          });
        });
        const id = items()[0].id;
        useActivityStore.getState().cancel(id);
        await stopped;
        return 0;
      },
    });

    await expect(pending).rejects.toBeTruthy();
    expect(items()[0].phase).toBe("cancelled");
    expect(items()[0].error).toBeUndefined();
  });

  it("exposes a cancel action while the job runs, and takes it away after", async () => {
    let seen = false;
    await runAsActivity({
      kind: "fetch",
      title: "one",
      run: async () => {
        seen = typeof items()[0].cancel === "function";
      },
    });
    expect(seen).toBe(true);
    expect(items()[0].cancel).toBeUndefined();
  });

  it("keeps the retry action so a failure can be tried again", async () => {
    let retries = 0;
    await expect(
      runAsActivity({
        kind: "fetch",
        title: "one",
        retry: () => {
          retries += 1;
        },
        run: async () => {
          throw new Error("nope");
        },
      }),
    ).rejects.toThrow();
    items()[0].retry?.();
    expect(retries).toBe(1);
  });

  it("records the jobs newest first", async () => {
    await runAsActivity({ kind: "fetch", title: "first", run: async () => 1 });
    await runAsActivity({ kind: "fetch", title: "second", run: async () => 2 });
    expect(items().map((item) => item.title)).toEqual(["second", "first"]);
  });
});
