// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { TaskView } from "@/components/task/TaskView";
import { fetchTaskCards, fetchTaskSummary } from "@/lib/api/task";
import type { TaskCardsView, TaskSummaryView } from "@/lib/api/types";
import { useTaskStore } from "@/stores/task-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/task", () => ({
  fetchTaskCards: vi.fn(),
  fetchTaskSummary: vi.fn(),
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
    error: "",
  });
  vi.mocked(fetchTaskSummary).mockReset();
  vi.mocked(fetchTaskCards).mockReset();
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

  it("says the task could not be read in the reader's words", async () => {
    vi.mocked(fetchTaskSummary).mockRejectedValue(new Error("boom"));
    vi.mocked(fetchTaskCards).mockRejectedValue(new Error("boom"));

    await draw();

    expect(host.textContent).toContain(i18n.t("task.loadFailed"));
  });
});
