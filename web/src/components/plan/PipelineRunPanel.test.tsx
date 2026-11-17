// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { PipelineRunPanel } from "@/components/plan/PipelineRunPanel";
import {
  cancelPipelineRun,
  fetchPipelineRuns,
  startPipelineRun,
  type PipelineRun,
} from "@/lib/api/pipeline";

vi.mock("@/lib/api/pipeline", () => ({
  fetchPipelineRuns: vi.fn(),
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
};

describe("PipelineRunPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(fetchPipelineRuns).mockResolvedValue({ runs: [] });
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
