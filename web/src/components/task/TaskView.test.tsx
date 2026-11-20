// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api/client";
import { TaskView } from "@/components/task/TaskView";
import {
  approveTaskRequest,
  fetchTaskApproval,
  fetchTaskCards,
  fetchTaskFrames,
  fetchTaskMaterials,
  fetchTaskSummary,
  frameTaskMaterial,
  pickTaskCard,
} from "@/lib/api/task";
import type {
  TaskApprovalView,
  TaskCardsView,
  TaskFramesView,
  TaskMaterialsView,
  TaskSummaryView,
  TaskWriteRequest,
} from "@/lib/api/types";
import { useTaskStore } from "@/stores/task-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/task", () => ({
  approveTaskRequest: vi.fn(),
  fetchTaskApproval: vi.fn(),
  fetchTaskCards: vi.fn(),
  fetchTaskFrames: vi.fn(),
  fetchTaskMaterials: vi.fn(),
  fetchTaskSummary: vi.fn(),
  frameTaskMaterial: vi.fn(),
  pickTaskCard: vi.fn(),
}));

const summary: TaskSummaryView = {
  view: "summary",
  rev: 2,
  id: "task-1",
  kind: "writing",
  intent: "write about entropy",
  status: "idle",
  updatedAt: "2026-11-10T09:00:00Z",
  counts: { cards: 2, materials: 0, frames: 0 },
  approval: null,
};

const cards: TaskCardsView = {
  view: "cards",
  rev: 2,
  omitted: 0,
  cards: [
    {
      id: "card-1",
      title: "Entropy",
      url: "https://example.com/entropy",
      snippet: "熵衡量不确定性。",
      site: "example.com",
    },
    {
      id: "card-2",
      title: "Information",
      url: "https://example.com/information",
      snippet: "信息是不确定性的减少。",
      site: "example.com",
    },
  ],
};

const noMaterials: TaskMaterialsView = {
  view: "materials",
  rev: 2,
  omitted: 0,
  materials: [],
};

const oneMaterial: TaskMaterialsView = {
  view: "materials",
  rev: 3,
  omitted: 0,
  materials: [
    {
      id: "raw/web/entropy-a1.html",
      title: "Entropy",
      url: "https://example.com/entropy",
      kind: "html",
      fidelity: "ok",
      state: "staged",
      blockCount: 12,
      blocksOmitted: 10,
      blocks: [
        { id: "b1", tag: "p", preview: "熵衡量不确定性。" },
        { id: "b2", tag: "p", preview: "信息是不确定性的减少。" },
      ],
    },
  ],
};

const PAGE_SPEC = {
  slug: "entropy",
  title: "Entropy",
  action: "create",
  sections: [
    { title: "What it is", blocks: ["b1"] },
    { title: "Why it matters", blocks: ["b2"] },
  ],
};

const pendingRequest: TaskWriteRequest = {
  frameId: "frame-1",
  spec: PAGE_SPEC,
  rev: 4,
  at: "2026-11-10T09:05:00Z",
};

const noApprovalView: TaskApprovalView = {
  view: "approval",
  rev: 2,
  status: "idle",
  approval: null,
  approvalStale: false,
  request: null,
  requestStale: false,
};

const requestView: TaskApprovalView = {
  view: "approval",
  rev: 4,
  status: "needs_approval",
  approval: null,
  approvalStale: false,
  request: pendingRequest,
  requestStale: false,
};

const noFrames: TaskFramesView = {
  view: "frames",
  rev: 2,
  omitted: 0,
  frames: [],
};

const oneFrame: TaskFramesView = {
  view: "frames",
  rev: 4,
  omitted: 0,
  frames: [
    {
      id: "frame-1",
      docId: "raw/web/entropy-a1.html",
      source: "human",
      blockCount: 2,
      blocks: [
        { id: "b1", preview: "熵衡量不确定性。" },
        { id: "b2", preview: "信息是不确定性的减少。" },
      ],
      blocksOmitted: 0,
    },
  ],
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  useWorkspaceStore.setState({ current: "demo" });
  useTaskStore.setState({
    status: "idle",
    summary: null,
    cards: null,
    materials: null,
    frames: null,
    approvalView: null,
    error: "",
    picking: "",
    pickError: "",
    pickNotice: "",
    framing: "",
    frameError: "",
    frameNotice: "",
    deciding: "",
    decisionError: "",
    decisionNotice: "",
  });
  vi.mocked(fetchTaskSummary).mockReset();
  vi.mocked(fetchTaskCards).mockReset();
  vi.mocked(fetchTaskMaterials).mockReset();
  vi.mocked(fetchTaskFrames).mockReset();
  vi.mocked(fetchTaskApproval).mockReset();
  vi.mocked(pickTaskCard).mockReset();
  vi.mocked(frameTaskMaterial).mockReset();
  vi.mocked(approveTaskRequest).mockReset();
  vi.mocked(fetchTaskMaterials).mockResolvedValue(noMaterials);
  vi.mocked(fetchTaskFrames).mockResolvedValue(noFrames);
  vi.mocked(fetchTaskApproval).mockResolvedValue(noApprovalView);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  useWorkspaceStore.setState({ current: null });
});

async function draw() {
  await act(async () => {
    root.render(<TaskView />);
  });
  await act(async () => {});
}

function buttonByText(text: string) {
  return [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(text),
  );
}

describe("the writing task view", () => {
  it("shows the revision, status and the cards the task holds", async () => {
    vi.mocked(fetchTaskSummary).mockResolvedValue(summary);
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);

    await draw();

    expect(host.textContent).toContain(i18n.t("task.rev", { rev: 2 }));
    expect(host.textContent).toContain(i18n.t("task.status.idle"));
    expect(host.textContent).toContain(i18n.t("task.cards", { count: 2 }));
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("Information");
    expect(host.textContent).toContain("example.com");
  });

  it("shows the task's materials with a bounded block index", async () => {
    vi.mocked(fetchTaskSummary).mockResolvedValue({
      ...summary,
      rev: 3,
      counts: { cards: 2, materials: 1, frames: 0 },
    });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);

    await draw();

    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain(i18n.t("task.blocks", { count: 12 }));
    expect(host.textContent).toContain("熵衡量不确定性。");
    expect(host.textContent).toContain("信息是不确定性的减少。");
    expect(host.textContent).toContain(i18n.t("task.moreBlocks", { count: 10 }));
  });

  it("picks a card as a person with the revision of the view they saw", async () => {
    vi.mocked(fetchTaskSummary).mockResolvedValue(summary);
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(pickTaskCard).mockResolvedValue({
      opId: "op-3",
      status: "done",
      rev: 3,
      material: oneMaterial.materials[0],
    });
    // The view is re-read after the pick; the second read has the material.
    vi.mocked(fetchTaskMaterials)
      .mockResolvedValueOnce(noMaterials)
      .mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce(summary)
      .mockResolvedValue({
        ...summary,
        rev: 3,
        counts: { cards: 2, materials: 1, frames: 0 },
      });

    await draw();
    const pick = [...host.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("task.pick")),
    );
    expect(pick).toBeTruthy();
    await act(async () => {
      pick?.click();
    });
    await act(async () => {});

    expect(vi.mocked(pickTaskCard)).toHaveBeenCalledWith("card-1", 2);
    expect(host.textContent).toContain("raw/web/entropy-a1.html");
  });

  it("says a pick lost the race in the reader's words and re-reads the task", async () => {
    vi.mocked(fetchTaskSummary).mockResolvedValue(summary);
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(pickTaskCard).mockRejectedValue(
      new ApiError("stale", 409, "conflict"),
    );

    await draw();
    const pick = [...host.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("task.pick")),
    );
    await act(async () => {
      pick?.click();
    });
    await act(async () => {});

    expect(host.textContent).toContain(i18n.t("error.code.conflict"));
    // A conflict is about a stale view: the view is read again.
    expect(vi.mocked(fetchTaskSummary).mock.calls.length).toBeGreaterThan(1);
  });

  it("says the task could not be read in the reader's words", async () => {
    vi.mocked(fetchTaskSummary).mockRejectedValue(new Error("boom"));
    vi.mocked(fetchTaskCards).mockRejectedValue(new Error("boom"));

    await draw();

    expect(host.textContent).toContain(i18n.t("task.loadFailed"));
  });

  it("shows the frames the task holds and who drew them", async () => {
    vi.mocked(fetchTaskSummary).mockResolvedValue({
      ...summary,
      rev: 4,
      counts: { cards: 2, materials: 1, frames: 1 },
    });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames).mockResolvedValue(oneFrame);

    await draw();

    expect(host.textContent).toContain(i18n.t("task.frames", { count: 1 }));
    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain(
      i18n.t("task.frameSource", { source: i18n.t("task.source.human") }),
    );
    expect(host.textContent).toContain(i18n.t("task.noApproval"));
  });

  it("draws a frame as a person with the revision of the view", async () => {
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce({
        ...summary,
        counts: { cards: 2, materials: 1, frames: 0 },
      })
      .mockResolvedValue({
        ...summary,
        rev: 3,
        counts: { cards: 2, materials: 1, frames: 1 },
      });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials)
      .mockResolvedValueOnce(oneMaterial)
      .mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames)
      .mockResolvedValueOnce(noFrames)
      .mockResolvedValue({ ...oneFrame, rev: 3 });
    vi.mocked(frameTaskMaterial).mockResolvedValue({
      opId: "op-4",
      status: "done",
      rev: 3,
      approvalCleared: false,
      frame: {
        id: "frame-1",
        docId: "raw/web/entropy-a1.html",
        blocks: ["b1"],
        source: "human",
      },
    });

    await draw();
    const toggle = host.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle?.click();
    });
    const save = [...host.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("task.frameSave")),
    );
    expect(save).toBeTruthy();
    await act(async () => {
      save?.click();
    });
    await act(async () => {});

    expect(vi.mocked(frameTaskMaterial)).toHaveBeenCalledWith(
      "raw/web/entropy-a1.html",
      ["b1"],
      2,
    );
    expect(host.textContent).toContain(i18n.t("task.frameSaved"));
  });

  it("makes an invalidated approval visible after a frame change", async () => {
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce({
        ...summary,
        rev: 3,
        counts: { cards: 2, materials: 1, frames: 1 },
        approval: {
          frameId: "frame-1",
          rev: 2,
          by: "human",
          at: "2026-11-10T09:05:00Z",
        },
      })
      .mockResolvedValue({
        ...summary,
        rev: 4,
        counts: { cards: 2, materials: 1, frames: 1 },
        approval: null,
      });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames)
      .mockResolvedValueOnce({ ...oneFrame, rev: 3 })
      .mockResolvedValue({ ...oneFrame, rev: 4 });
    vi.mocked(frameTaskMaterial).mockResolvedValue({
      opId: "op-5",
      status: "done",
      rev: 4,
      approvalCleared: true,
      frame: {
        id: "frame-1",
        docId: "raw/web/entropy-a1.html",
        blocks: ["b2"],
        source: "human",
      },
    });

    await draw();
    const approved = i18n.t("task.approved", {
      by: i18n.t("task.source.human"),
      rev: 2,
    });
    expect(host.textContent).toContain(approved);

    // The frame already covers b1 and b2; unticking b1 changes it.
    const toggle = host.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    expect(toggle?.checked).toBe(true);
    await act(async () => {
      toggle?.click();
    });
    const save = [...host.querySelectorAll("button")].find((item) =>
      item.textContent?.includes(i18n.t("task.frameSave")),
    );
    await act(async () => {
      save?.click();
    });
    await act(async () => {});

    expect(vi.mocked(frameTaskMaterial)).toHaveBeenCalledWith(
      "raw/web/entropy-a1.html",
      ["b2"],
      3,
    );
    expect(host.textContent).toContain(i18n.t("task.approvalInvalidated"));
    expect(host.textContent).not.toContain(approved);
  });

  it("shows the write request with its sections and evidence, and approves it", async () => {
    const approval = {
      frameId: "frame-1",
      spec: PAGE_SPEC,
      rev: 5,
      by: "human",
      at: "2026-11-10T09:06:00Z",
    };
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce({
        ...summary,
        rev: 4,
        status: "needs_approval",
        counts: { cards: 2, materials: 1, frames: 1 },
      })
      .mockResolvedValue({
        ...summary,
        rev: 5,
        counts: { cards: 2, materials: 1, frames: 1 },
        approval,
      });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames).mockResolvedValue(oneFrame);
    vi.mocked(fetchTaskApproval)
      .mockResolvedValueOnce(requestView)
      .mockResolvedValue({
        ...noApprovalView,
        rev: 5,
        approval,
      });
    vi.mocked(approveTaskRequest).mockResolvedValue({
      decision: "approve",
      rev: 5,
      approval,
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("task.status.needs_approval"));
    expect(host.textContent).toContain(i18n.t("task.writeRequest"));
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain(i18n.t("task.action.create"));
    expect(host.textContent).toContain("What it is");
    expect(host.textContent).toContain("Why it matters");
    // The evidence is the bounded preview of each block the section cites.
    expect(host.textContent).toContain("熵衡量不确定性。");
    expect(host.textContent).toContain("信息是不确定性的减少。");

    const approve = buttonByText(i18n.t("task.approve"));
    expect(approve).toBeTruthy();
    await act(async () => {
      approve?.click();
    });
    await act(async () => {});

    expect(vi.mocked(approveTaskRequest)).toHaveBeenCalledWith(
      "task-1",
      4,
      "frame-1",
      PAGE_SPEC,
      "approve",
    );
    expect(host.textContent).toContain(i18n.t("task.approvedNotice"));
  });

  it("rejects the write request and lets the agent propose again", async () => {
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce({
        ...summary,
        rev: 4,
        status: "needs_approval",
      })
      .mockResolvedValue({ ...summary, rev: 5 });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames).mockResolvedValue(oneFrame);
    vi.mocked(fetchTaskApproval)
      .mockResolvedValueOnce(requestView)
      .mockResolvedValue({ ...noApprovalView, rev: 5 });
    vi.mocked(approveTaskRequest).mockResolvedValue({
      decision: "reject",
      rev: 5,
    });

    await draw();
    const reject = buttonByText(i18n.t("task.reject"));
    expect(reject).toBeTruthy();
    await act(async () => {
      reject?.click();
    });
    await act(async () => {});

    expect(vi.mocked(approveTaskRequest)).toHaveBeenCalledWith(
      "task-1",
      4,
      "frame-1",
      PAGE_SPEC,
      "reject",
    );
    expect(host.textContent).toContain(i18n.t("task.rejectedNotice"));
    expect(host.textContent).not.toContain(i18n.t("task.writeRequest"));
  });

  it("hides the write request when a frame change invalidates it", async () => {
    vi.mocked(fetchTaskSummary)
      .mockResolvedValueOnce({
        ...summary,
        rev: 4,
        status: "needs_approval",
        counts: { cards: 2, materials: 1, frames: 1 },
      })
      .mockResolvedValue({
        ...summary,
        rev: 5,
        counts: { cards: 2, materials: 1, frames: 1 },
      });
    vi.mocked(fetchTaskCards).mockResolvedValue(cards);
    vi.mocked(fetchTaskMaterials).mockResolvedValue(oneMaterial);
    vi.mocked(fetchTaskFrames)
      .mockResolvedValueOnce(oneFrame)
      .mockResolvedValue({ ...oneFrame, rev: 5 });
    vi.mocked(fetchTaskApproval)
      .mockResolvedValueOnce(requestView)
      .mockResolvedValue({ ...noApprovalView, rev: 5 });
    vi.mocked(frameTaskMaterial).mockResolvedValue({
      opId: "op-6",
      status: "done",
      rev: 5,
      approvalCleared: true,
      frame: {
        id: "frame-1",
        docId: "raw/web/entropy-a1.html",
        blocks: ["b2"],
        source: "human",
      },
    });

    await draw();
    expect(host.textContent).toContain(i18n.t("task.writeRequest"));

    // The frame already covers b1 and b2; unticking b1 changes the basis the
    // request was written from.
    const toggle = host.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    expect(toggle?.checked).toBe(true);
    await act(async () => {
      toggle?.click();
    });
    const save = buttonByText(i18n.t("task.frameSave"));
    await act(async () => {
      save?.click();
    });
    await act(async () => {});

    expect(host.textContent).toContain(i18n.t("task.approvalInvalidated"));
    expect(host.textContent).not.toContain(i18n.t("task.writeRequest"));
  });
});
