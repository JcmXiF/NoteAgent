// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { PipelineRunPanel } from "@/components/plan/PipelineRunPanel";
import {
  cancelPipelineRun,
  fetchPipelineMaterials,
  fetchPipelineRestorePreview,
  fetchPipelineRuns,
  restorePipelineRun,
  startPipelineRun,
  type PipelineRun,
} from "@/lib/api/pipeline";

vi.mock("@/lib/api/pipeline", () => ({
  fetchPipelineRuns: vi.fn(),
  fetchPipelineMaterials: vi.fn(),
  fetchPipelineConfirmation: vi.fn(),
  fetchPipelineRestorePreview: vi.fn(),
  restorePipelineRun: vi.fn(),
  startPipelineRun: vi.fn(),
  cancelPipelineRun: vi.fn(),
}));

const running: PipelineRun = {
  id: "pr-1",
  intent: "写一篇熵的笔记",
  startedBy: "human",
  sessionId: "",
  queryMode: "precise",
  materialMode: "auto",
  status: "running",
  stage: "frame",
  startedAt: "2026-11-10T09:00:00Z",
  updatedAt: "2026-11-10T09:00:01Z",
  planRevision: 0,
  error: "",
  detail: {},
};

const finished: PipelineRun = {
  ...running,
  status: "succeeded",
  stage: "done",
  planRevision: 1,
  updatedAt: "2026-11-10T09:05:00Z",
  result: {
    planRevision: 1,
    written: ["wiki/entropy.md"],
    skipped: [],
    failed: [],
    effectIds: ["eff-1"],
  },
};

describe("PipelineRunPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(fetchPipelineMaterials).mockResolvedValue({ materials: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  async function render(): Promise<void> {
    await act(async () => {
      root.render(<PipelineRunPanel />);
    });
    await act(async () => {});
  }

  function button(label: string): HTMLButtonElement {
    const found = [...container.querySelectorAll("button")].find(
      (item) => item.textContent?.trim() === label,
    );
    if (!found) throw new Error(`no button labelled ${label}`);
    return found as HTMLButtonElement;
  }

  it("previews and restores a finished run's pages", async () => {
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [finished] });
    vi.mocked(fetchPipelineRestorePreview).mockResolvedValue({
      runId: "pr-1",
      pages: [
        {
          path: "wiki/entropy.md",
          kind: "wiki_page_write",
          runId: "pr-1",
          at: "2026-11-10T09:05:00Z",
          classification: "unchanged",
          defaultAction: "restore",
          effectId: "eff-1",
          before: "old",
          after: "new",
          currentPath: "",
        },
      ],
    });
    vi.mocked(restorePipelineRun).mockResolvedValue({
      runId: "pr-1",
      restored: [
        {
          path: "wiki/entropy.md",
          classification: "unchanged",
          effectId: "eff-2",
          before: "new",
          after: "old",
        },
      ],
      skipped: [],
      conflicted: [],
      failed: [],
    });
    await render();

    await act(async () => {
      button(i18n.t("pipeline.restore.open")).click();
    });
    await act(async () => {});

    expect(container.textContent).toContain("wiki/entropy.md");
    expect(container.textContent).toContain(
      i18n.t("pipeline.restore.classification.unchanged"),
    );

    await act(async () => {
      button(i18n.t("pipeline.restore.apply")).click();
    });
    await act(async () => {});

    expect(vi.mocked(restorePipelineRun).mock.calls[0][0]).toBe("pr-1");
    expect(vi.mocked(restorePipelineRun).mock.calls[0][1]).toEqual([
      { path: "wiki/entropy.md", action: "restore", force: false },
    ]);
    expect(container.textContent).toContain(
      i18n.t("pipeline.restore.done", { count: 1 }),
    );
  });

  it("shows the latest run's intent, status and stage", async () => {
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [running] });

    await render();

    expect(container.textContent).toContain("写一篇熵的笔记");
    expect(container.textContent).toContain(i18n.t("pipeline.status.running"));
    expect(container.textContent).toContain(i18n.t("pipeline.stage.frame"));
  });

  it("starts a run with the intent the reader typed", async () => {
    vi.mocked(startPipelineRun).mockResolvedValue(running);
    await render();

    const input = container.querySelector("input") as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "写一篇熵的笔记");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      button(i18n.t("pipeline.start")).click();
    });

    expect(vi.mocked(startPipelineRun).mock.calls[0][0].intent).toBe(
      "写一篇熵的笔记",
    );
  });

  it("starts with the query mode the reader chose", async () => {
    vi.mocked(startPipelineRun).mockResolvedValue(running);
    await render();

    await act(async () => {
      button(i18n.t("pipeline.query.model")).click();
    });
    const input = container.querySelector("input") as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "写一篇熵的笔记");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      button(i18n.t("pipeline.start")).click();
    });

    const call = vi.mocked(startPipelineRun).mock.calls[0][0];
    expect(call.queryMode).toBe("model");
    expect(call.materialMode).toBe("auto");
  });

  it("lists the materials the run collected", async () => {
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [running] });
    vi.mocked(fetchPipelineMaterials).mockResolvedValue({
      materials: [
        {
          material: "raw/web/entropy-a1.html",
          state: "staged",
          runId: "pr-1",
          updatedAt: "2026-11-10T09:00:01Z",
          title: "熵",
          kind: "html",
          fidelity: "ok",
        },
      ],
    });

    await render();

    expect(container.textContent).toContain("熵");
    expect(container.textContent).toContain(i18n.t("pipeline.material.staged"));
  });

  it("cancels the active run", async () => {
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [running] });
    vi.mocked(cancelPipelineRun).mockResolvedValue({
      ...running,
      status: "cancelled",
    });
    await render();

    await act(async () => {
      button(i18n.t("pipeline.cancel")).click();
    });

    expect(cancelPipelineRun).toHaveBeenCalledWith("pr-1");
  });
});
