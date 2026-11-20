// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api/client";
import { SessionCards } from "@/components/chat/SessionCards";
import { SessionView } from "@/components/chat/SessionView";
import { TaskView } from "@/components/task/TaskView";
import {
  approveTaskRequest,
  cancelTaskOp,
  fetchTaskApproval,
  fetchTaskCards,
  fetchTaskFrames,
  fetchTaskMaterials,
  fetchTaskOverview,
  fetchTaskResult,
  fetchTaskSummary,
  frameTaskMaterial,
  pickTaskCard,
  resumeTaskOp,
  searchTaskCards,
  subscribeTaskEvents,
} from "@/lib/api/task";
import {
  fetchSession,
  postSessionQuery,
} from "@/lib/api/sessions";
import type {
  SessionPayload,
  TaskApprovalView,
  TaskCardsView,
  TaskFramesView,
  TaskMaterialsView,
  TaskOpSummary,
  TaskOverviewView,
  TaskResultView,
  TaskSummaryView,
  TaskWriteRequest,
} from "@/lib/api/types";
import { useTaskStore } from "@/stores/task-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/task", () => ({
  approveTaskRequest: vi.fn(),
  cancelTaskOp: vi.fn(),
  fetchTaskApproval: vi.fn(),
  fetchTaskCards: vi.fn(),
  fetchTaskFrames: vi.fn(),
  fetchTaskMaterials: vi.fn(),
  fetchTaskOverview: vi.fn(),
  fetchTaskResult: vi.fn(),
  fetchTaskSummary: vi.fn(),
  frameTaskMaterial: vi.fn(),
  pickTaskCard: vi.fn(),
  resumeTaskOp: vi.fn(),
  searchTaskCards: vi.fn(),
  subscribeTaskEvents: vi.fn(),
}));

vi.mock("@/lib/api/sessions", () => ({
  fetchSession: vi.fn(),
  postSessionCancel: vi.fn(),
  postSessionQuery: vi.fn(),
  postSessionWaitCancel: vi.fn(),
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
  ops: [],
  opsOmitted: 0,
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

const noResultView: TaskResultView = {
  view: "result",
  rev: 2,
  status: "idle",
  result: null,
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

/**
 * One snapshot, as the server sends it: every view carries the same revision.
 *
 * A part may name its own data; its `rev` is always the snapshot's, because the
 * point of the overview is that the panel never mixes two revisions.
 */
function snapshot(parts: {
  rev?: number;
  summary?: Partial<TaskSummaryView>;
  cards?: TaskCardsView;
  materials?: TaskMaterialsView;
  frames?: TaskFramesView;
  approval?: TaskApprovalView;
  result?: TaskResultView;
} = {}): TaskOverviewView {
  const rev = parts.rev ?? parts.summary?.rev ?? 2;
  return {
    view: "overview",
    rev,
    summary: { ...summary, ...parts.summary, rev },
    cards: { ...cards, ...parts.cards, rev },
    materials: { ...noMaterials, ...parts.materials, rev },
    frames: { ...noFrames, ...parts.frames, rev },
    approval: { ...noApprovalView, ...parts.approval, rev },
    result: { ...noResultView, ...parts.result, rev },
  };
}

const sessionPayload: SessionPayload = {
  id: "s1",
  title: "Writing",
  updatedAt: "2026-11-10T09:00:00Z",
  state: { busy: false },
  messages: [],
  derived: {
    sections: {},
    summary: "",
    messages: [],
    diagnostics: [],
  },
};

/** The handlers of each stream the store opened, newest last. */
type StreamHandlers = {
  onEvent: (rev: number) => void;
  onOpen: () => void;
};

let host: HTMLDivElement;
let root: Root;
let streams: StreamHandlers[];
let closeStream: Mock<() => void>;

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
    resultView: null,
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
    opBusy: "",
    opError: "",
    opNotice: "",
    searching: false,
    searchError: "",
    searchNotice: "",
  });
  vi.mocked(fetchTaskOverview).mockReset();
  vi.mocked(fetchTaskSummary).mockReset();
  vi.mocked(fetchTaskCards).mockReset();
  vi.mocked(fetchTaskMaterials).mockReset();
  vi.mocked(fetchTaskFrames).mockReset();
  vi.mocked(fetchTaskApproval).mockReset();
  vi.mocked(fetchTaskResult).mockReset();
  vi.mocked(pickTaskCard).mockReset();
  vi.mocked(frameTaskMaterial).mockReset();
  vi.mocked(approveTaskRequest).mockReset();
  vi.mocked(cancelTaskOp).mockReset();
  vi.mocked(resumeTaskOp).mockReset();
  vi.mocked(searchTaskCards).mockReset();
  vi.mocked(fetchSession).mockReset();
  vi.mocked(postSessionQuery).mockReset();
  vi.mocked(subscribeTaskEvents).mockReset();
  streams = [];
  closeStream = vi.fn<() => void>();
  vi.mocked(subscribeTaskEvents).mockImplementation((handlers) => {
    streams.push(handlers);
    // A real EventSource reports its open after the connection is made; the
    // first snapshot is read then, not on a separate mount path.
    handlers.onOpen();
    return closeStream;
  });
  vi.mocked(fetchTaskOverview).mockResolvedValue(snapshot());
});

afterEach(() => {
  act(() => {
    root.unmount();
    useTaskStore.getState().disconnect();
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
  it("reads the whole task in one snapshot and shows its one revision", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({ summary, cards }),
    );

    await draw();

    expect(host.textContent).toContain(i18n.t("task.rev", { rev: 2 }));
    expect(host.textContent).toContain(i18n.t("task.status.idle"));
    expect(host.textContent).toContain(i18n.t("task.cards", { count: 2 }));
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("Information");
    expect(host.textContent).toContain("example.com");
    // One read, one revision: a write between separate reads cannot mix them.
    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetchTaskSummary)).not.toHaveBeenCalled();
    expect(vi.mocked(fetchTaskCards)).not.toHaveBeenCalled();
    expect(vi.mocked(fetchTaskMaterials)).not.toHaveBeenCalled();
    expect(vi.mocked(fetchTaskFrames)).not.toHaveBeenCalled();
    expect(vi.mocked(fetchTaskApproval)).not.toHaveBeenCalled();
    expect(vi.mocked(fetchTaskResult)).not.toHaveBeenCalled();
  });

  it("shows the last approved page write and its diff", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        rev: 4,
        summary: { ...summary, status: "done" },
        result: {
          view: "result",
          rev: 4,
          status: "done",
          result: {
            status: "written",
            page: "wiki/entropy.md",
            slug: "entropy",
            title: "Entropy",
            action: "create",
            summary: "written wiki/entropy.md (create, 244 bytes)",
            reason: "",
            written: ["wiki/entropy.md"],
            skipped: [],
            failed: [],
            diff: "@@ -0,0 +1,3 @@\n+# Entropy\n+composed from b1",
            added: 2,
            removed: 0,
          },
        },
      }),
    );

    await draw();

    expect(host.textContent).toContain(i18n.t("task.writeResult"));
    expect(host.textContent).toContain("wiki/entropy.md");
    expect(host.textContent).toContain(
      "written wiki/entropy.md (create, 244 bytes)",
    );
    // The diff is the page's prose as the interface shows it; the task view
    // is the one place it lives (the chat card carries only the outcome).
    expect(host.textContent).toContain("+composed from b1");
    expect(host.textContent).toContain(i18n.t("task.writeStatus.written"));
  });

  it("says a page write failed and why", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        rev: 4,
        summary: { ...summary, status: "failed" },
        result: {
          view: "result",
          rev: 4,
          status: "failed",
          result: {
            status: "failed",
            page: "wiki/entropy.md",
            slug: "entropy",
            title: "Entropy",
            action: "create",
            summary: "could not write wiki/entropy.md: the disk is full",
            reason: "the disk is full",
            written: [],
            skipped: [],
            failed: [{ path: "wiki/entropy.md", reason: "the disk is full" }],
            diff: "",
            added: 0,
            removed: 0,
          },
        },
      }),
    );

    await draw();

    expect(host.textContent).toContain(i18n.t("task.writeStatus.failed"));
    expect(host.textContent).toContain("the disk is full");
  });

  it("shows the task's materials with a bounded block index", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        rev: 3,
        summary: { ...summary, counts: { cards: 2, materials: 1, frames: 0 } },
        materials: oneMaterial,
      }),
    );

    await draw();

    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain(i18n.t("task.blocks", { count: 12 }));
    expect(host.textContent).toContain("熵衡量不确定性。");
    expect(host.textContent).toContain("信息是不确定性的减少。");
    expect(host.textContent).toContain(i18n.t("task.moreBlocks", { count: 10 }));
  });

  it("picks a card as a person with the revision of the view they saw", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(snapshot({ summary, cards }));
    vi.mocked(pickTaskCard).mockResolvedValue({
      opId: "op-3",
      status: "done",
      rev: 3,
      material: oneMaterial.materials[0],
    });
    // The view is re-read after the pick; the second snapshot has the material.
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(snapshot({ summary, cards }))
      .mockResolvedValue(
        snapshot({
          rev: 3,
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 0 },
          },
          cards,
          materials: oneMaterial,
        }),
      );

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
    vi.mocked(fetchTaskOverview).mockResolvedValue(snapshot({ summary, cards }));
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
    expect(vi.mocked(fetchTaskOverview).mock.calls.length).toBeGreaterThan(1);
  });

  it("says the task could not be read in the reader's words", async () => {
    vi.mocked(fetchTaskOverview).mockRejectedValue(new Error("boom"));

    await draw();

    expect(host.textContent).toContain(i18n.t("task.loadFailed"));
  });

  it("shows the frames the task holds and who drew them", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        rev: 4,
        summary: { ...summary, counts: { cards: 2, materials: 1, frames: 1 } },
        materials: oneMaterial,
        frames: oneFrame,
      }),
    );

    await draw();

    expect(host.textContent).toContain(i18n.t("task.frames", { count: 1 }));
    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain(
      i18n.t("task.frameSource", { source: i18n.t("task.source.human") }),
    );
    expect(host.textContent).toContain(i18n.t("task.noApproval"));
  });

  it("draws a frame as a person with the revision of the view", async () => {
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 0 },
          },
          materials: oneMaterial,
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 3,
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: { ...oneFrame, rev: 3 },
        }),
      );
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
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          rev: 3,
          summary: {
            ...summary,
            rev: 3,
            counts: { cards: 2, materials: 1, frames: 1 },
            approval: {
              frameId: "frame-1",
              rev: 2,
              by: "human",
              at: "2026-11-10T09:05:00Z",
            },
          },
          materials: oneMaterial,
          frames: { ...oneFrame, rev: 3 },
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 4,
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: { ...oneFrame, rev: 4 },
        }),
      );
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
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          rev: 4,
          summary: {
            ...summary,
            status: "needs_approval",
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: oneFrame,
          approval: requestView,
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 5,
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 1 },
            approval,
          },
          materials: oneMaterial,
          frames: oneFrame,
        }),
      );
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
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          rev: 4,
          summary: { ...summary, status: "needs_approval" },
          materials: oneMaterial,
          frames: oneFrame,
          approval: requestView,
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 5,
          summary,
          materials: oneMaterial,
          frames: oneFrame,
        }),
      );
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
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          rev: 4,
          summary: {
            ...summary,
            status: "needs_approval",
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: oneFrame,
          approval: requestView,
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 5,
          summary: {
            ...summary,
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: { ...oneFrame, rev: 5 },
        }),
      );
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

  const activeOp: TaskOpSummary = {
    id: "op-3",
    kind: "write",
    actor: "agent",
    status: "running",
    createdAt: "2026-11-10T09:00:00Z",
    updatedAt: "2026-11-10T09:01:00Z",
    interrupted: false,
    resumeRequested: true,
    resumeStarted: false,
    resumeError: "",
    errorCode: "",
    error: "",
  };

  it("shows an active op and cancels it from the panel", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({ summary: { ...summary, ops: [activeOp] } }),
    );
    vi.mocked(cancelTaskOp).mockResolvedValue({
      cancelled: true,
      rev: 2,
      op: { ...activeOp, status: "cancelled", resumeRequested: false },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("task.ops"));
    expect(host.textContent).toContain("op-3");
    const cancel = buttonByText(i18n.t("task.opCancel"));
    expect(cancel).toBeTruthy();
    await act(async () => {
      cancel?.click();
    });
    await act(async () => {});

    expect(vi.mocked(cancelTaskOp)).toHaveBeenCalledWith("op-3");
    expect(host.textContent).toContain(
      i18n.t("task.opCancelledNotice", { id: "op-3" }),
    );
    // The panel re-read the task after the action.
    expect(vi.mocked(fetchTaskOverview).mock.calls.length).toBeGreaterThan(1);
  });

  it("shows an interrupted op and resumes it from the panel", async () => {
    const interrupted = {
      ...activeOp,
      interrupted: true,
      resumeStarted: false,
    };
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({ summary: { ...summary, ops: [interrupted] } }),
    );
    vi.mocked(resumeTaskOp).mockResolvedValue({
      resumed: true,
      rev: 2,
      op: { ...interrupted, interrupted: false },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("task.opInterrupted"));
    const resume = buttonByText(i18n.t("task.opResume"));
    expect(resume).toBeTruthy();
    await act(async () => {
      resume?.click();
    });
    await act(async () => {});

    expect(vi.mocked(resumeTaskOp)).toHaveBeenCalledWith("op-3");
    expect(host.textContent).toContain(
      i18n.t("task.opResumedNotice", { id: "op-3" }),
    );
  });

  it("offers no control for a finished op", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        summary: {
          ...summary,
          ops: [
            {
              ...activeOp,
              status: "done",
              resumeRequested: false,
              resumeStarted: true,
            },
          ],
        },
      }),
    );

    await draw();

    expect(host.textContent).toContain("op-3");
    expect(buttonByText(i18n.t("task.opCancel"))).toBeUndefined();
    expect(buttonByText(i18n.t("task.opResume"))).toBeUndefined();
  });

  it("searches the web as a person with the revision of the view", async () => {
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(snapshot({ summary, cards }))
      .mockResolvedValue(snapshot({ rev: 3, summary, cards }));
    vi.mocked(searchTaskCards).mockResolvedValue({
      opId: "op-1",
      query: "entropy",
      status: "done",
      rev: 3,
      cards: cards.cards,
    });

    await draw();
    const input = host.querySelector<HTMLInputElement>("input[type='text']");
    expect(input).toBeTruthy();
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, "entropy");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const form = host.querySelector("form");
    expect(form).toBeTruthy();
    await act(async () => {
      form?.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {});

    // The human search names the revision of the cards view the person saw.
    expect(vi.mocked(searchTaskCards)).toHaveBeenCalledWith("entropy", 2);
    expect(host.textContent).toContain(
      i18n.t("task.searchDone", { count: 2 }),
    );
    // The one task was read again, not patched from the response.
    expect(vi.mocked(fetchTaskOverview).mock.calls.length).toBeGreaterThan(1);
  });

  it("says a search lost the race in the reader's words and re-reads", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(snapshot({ summary, cards }));
    vi.mocked(searchTaskCards).mockRejectedValue(
      new ApiError("stale", 409, "conflict"),
    );

    await draw();
    const input = host.querySelector<HTMLInputElement>("input[type='text']");
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, "entropy");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      host.querySelector("form")?.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {});

    expect(host.textContent).toContain(i18n.t("error.code.conflict"));
    expect(vi.mocked(fetchTaskOverview).mock.calls.length).toBeGreaterThan(1);
  });

  it("shows the same write request as the chat card", async () => {
    vi.mocked(fetchTaskOverview).mockResolvedValue(
      snapshot({
        rev: 4,
        summary: {
          ...summary,
          status: "needs_approval",
          counts: { cards: 2, materials: 1, frames: 1 },
        },
        materials: oneMaterial,
        frames: oneFrame,
        approval: requestView,
      }),
    );

    await draw();
    const chatHost = document.createElement("div");
    document.body.append(chatHost);
    const chatRoot = createRoot(chatHost);
    const review = vi.fn();
    try {
      await act(async () => {
        chatRoot.render(
          <SessionCards
            observations={[
              {
                tool: "compose_page",
                status: "needs_approval",
                rev: 4,
                title: "Entropy",
                request: pendingRequest,
              },
            ]}
            onReviewTask={review}
          />,
        );
      });

      // The panel and the chat card read the one request the task stores: the
      // page, its sections and the revision the person decides at.
      expect(host.textContent).toContain(i18n.t("task.writeRequest"));
      expect(host.textContent).toContain("What it is");
      expect(host.textContent).toContain("Why it matters");
      expect(chatHost.textContent).toContain("Entropy");
      expect(chatHost.textContent).toContain("What it is");
      expect(chatHost.textContent).toContain(i18n.t("chat.looked.needsApproval"));
      // The chat card is a pointer to the gate, not a second decision path.
      const reviewButton = [...chatHost.querySelectorAll("button")].find(
        (button) =>
          button.textContent?.includes(i18n.t("chat.reviewTask")),
      );
      expect(reviewButton).toBeTruthy();
      await act(async () => {
        reviewButton?.click();
      });
      expect(review).toHaveBeenCalled();
    } finally {
      await act(async () => {
        chatRoot.unmount();
      });
      chatHost.remove();
    }
  });
});

describe("the task change stream", () => {
  it("refreshes the one snapshot once for a burst of events", async () => {
    await draw();
    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(1);
    expect(streams).toHaveLength(1);

    await act(async () => {
      streams[0].onEvent(3);
      streams[0].onEvent(4);
      // The debounce coalesces the burst into one snapshot read.
      await new Promise((resolve) => setTimeout(resolve, 80));
    });

    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(2);
  });

  it("reads the task even when the stream never opens", async () => {
    // The stream is additive: a route, proxy or version mismatch may leave the
    // EventSource connecting forever while the REST overview still works. The
    // panel must not stay empty waiting for an `open` that never comes.
    vi.mocked(subscribeTaskEvents).mockImplementation((handlers) => {
      streams.push(handlers);
      return closeStream;
    });

    await draw();
    await act(async () => {});

    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain(i18n.t("task.rev", { rev: 2 }));
  });

  it("reads the task again when the stream reconnects", async () => {
    await draw();
    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(1);

    await act(async () => {
      streams[0].onOpen();
    });
    await act(async () => {});

    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(2);
  });

  it("closes the old stream when the project changes", async () => {
    await draw();
    expect(streams).toHaveLength(1);
    expect(closeStream).not.toHaveBeenCalled();

    await act(async () => {
      useWorkspaceStore.setState({ current: "other" });
    });
    await act(async () => {});

    expect(closeStream).toHaveBeenCalledTimes(1);
    expect(streams).toHaveLength(2);

    // The stream that belonged to the old project cannot make the new panel
    // read; only the one opened for this project can.
    const reads = vi.mocked(fetchTaskOverview).mock.calls.length;
    streams[0].onEvent(9);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
    });
    expect(vi.mocked(fetchTaskOverview)).toHaveBeenCalledTimes(reads);
  });

  it("closes the stream when the panel unmounts", async () => {
    const unmountHost = document.createElement("div");
    document.body.append(unmountHost);
    const unmountRoot = createRoot(unmountHost);
    await act(async () => {
      unmountRoot.render(<TaskView />);
    });
    await act(async () => {});
    expect(streams).toHaveLength(1);

    act(() => {
      unmountRoot.unmount();
    });
    unmountHost.remove();

    expect(closeStream).toHaveBeenCalledTimes(1);
  });
});

describe("the panel and the chat read one task", () => {
  async function drawChat(chatHost: HTMLDivElement) {
    const chatRoot = createRoot(chatHost);
    await act(async () => {
      chatRoot.render(<SessionView sessionId="s1" slot="main" />);
    });
    await act(async () => {});
    return chatRoot;
  }

  async function askInChat(chatHost: HTMLDivElement, question: string) {
    const textarea = chatHost.querySelector("textarea");
    expect(textarea).not.toBeNull();
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    await act(async () => {
      setter?.call(textarea, question);
      textarea?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      chatHost
        .querySelector("form")
        ?.dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }),
        );
    });
    await act(async () => {});
    await act(async () => {});
  }

  it("shows a request that arrives while the panel is mounted", async () => {
    vi.mocked(fetchTaskOverview)
      .mockResolvedValueOnce(
        snapshot({
          rev: 4,
          summary: { ...summary, rev: 4 },
          materials: oneMaterial,
          frames: oneFrame,
        }),
      )
      .mockResolvedValue(
        snapshot({
          rev: 4,
          summary: {
            ...summary,
            status: "needs_approval",
            counts: { cards: 2, materials: 1, frames: 1 },
          },
          materials: oneMaterial,
          frames: oneFrame,
          approval: requestView,
        }),
      );
    vi.mocked(fetchSession).mockResolvedValue(sessionPayload);
    vi.mocked(postSessionQuery).mockImplementation(
      (_sessionId, _content, handlers) => {
        // The agent's compose_page opens the gate while the panel is mounted.
        handlers?.onObservation?.({
          tool: "compose_page",
          status: "needs_approval",
          rev: 4,
          title: "Entropy",
          request: pendingRequest,
        });
        return Promise.resolve(sessionPayload);
      },
    );
    vi.mocked(approveTaskRequest).mockResolvedValue({
      decision: "approve",
      rev: 5,
    });

    await draw();
    expect(host.textContent).not.toContain(i18n.t("task.writeRequest"));

    const chatHost = document.createElement("div");
    document.body.append(chatHost);
    const chatRoot = await drawChat(chatHost);
    try {
      await askInChat(chatHost, "写一页");

      // No remount: the observation refreshed the one task store.
      expect(host.textContent).toContain(i18n.t("task.writeRequest"));
      expect(host.textContent).toContain("What it is");
      const approve = buttonByText(i18n.t("task.approve"));
      const reject = buttonByText(i18n.t("task.reject"));
      expect(approve).toBeTruthy();
      expect(reject).toBeTruthy();

      // Approving in the panel calls the one human-only route.
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
    } finally {
      await act(async () => {
        chatRoot.unmount();
      });
      chatHost.remove();
    }
  });
});
