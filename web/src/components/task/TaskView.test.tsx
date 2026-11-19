// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api/client";
import { TaskView } from "@/components/task/TaskView";
import {
  fetchTaskCards,
  fetchTaskMaterials,
  fetchTaskSummary,
  pickTaskCard,
} from "@/lib/api/task";
import type {
  TaskCardsView,
  TaskMaterialsView,
  TaskSummaryView,
} from "@/lib/api/types";
import { useTaskStore } from "@/stores/task-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/task", () => ({
  fetchTaskCards: vi.fn(),
  fetchTaskMaterials: vi.fn(),
  fetchTaskSummary: vi.fn(),
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
    error: "",
    picking: "",
    pickError: "",
    pickNotice: "",
  });
  vi.mocked(fetchTaskSummary).mockReset();
  vi.mocked(fetchTaskCards).mockReset();
  vi.mocked(fetchTaskMaterials).mockReset();
  vi.mocked(pickTaskCard).mockReset();
  vi.mocked(fetchTaskMaterials).mockResolvedValue(noMaterials);
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
});
