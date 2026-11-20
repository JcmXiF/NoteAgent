// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { PageEditDialog } from "@/components/docs/PageEditDialog";
import { fetchPageEdit, savePageBody } from "@/lib/api/pages";
import {
  fetchTaskApproval,
  fetchTaskCards,
  fetchTaskFrames,
  fetchTaskMaterials,
  fetchTaskResult,
  fetchTaskSummary,
} from "@/lib/api/task";
import type { TaskSummaryView } from "@/lib/api/types";
import { useDocsStore } from "@/stores/docs-store";
import { useTaskStore } from "@/stores/task-store";

vi.mock("@/lib/api/pages", () => ({
  fetchPageEdit: vi.fn(),
  savePageBody: vi.fn(),
}));

vi.mock("@/lib/api/task", () => ({
  fetchTaskSummary: vi.fn(),
  fetchTaskCards: vi.fn(),
  fetchTaskMaterials: vi.fn(),
  fetchTaskFrames: vi.fn(),
  fetchTaskApproval: vi.fn(),
  fetchTaskResult: vi.fn(),
}));

const summary: TaskSummaryView = {
  view: "summary",
  rev: 4,
  id: "task-1",
  kind: "writing",
  intent: "write about entropy",
  status: "idle",
  updatedAt: "2026-11-10T09:00:00Z",
  counts: { cards: 0, materials: 0, frames: 1 },
  approval: null,
  ops: [],
  opsOmitted: 0,
};

const view = {
  path: "wiki/entropy.md",
  title: "Entropy",
  body: "old prose",
  frontmatter: "---\ntitle: Entropy\n---",
  revision: "rev-1",
  backlinkCount: 0,
  noteCount: 0,
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  // The dialog refreshes the docs tree after a save; a null project makes that
  // a no-op, and the task store is already showing a task.
  useDocsStore.setState({ project: null });
  useTaskStore.setState({
    status: "ready",
    summary,
    cards: null,
    materials: null,
    frames: null,
    approvalView: null,
    resultView: null,
    error: "",
  });
  vi.mocked(fetchPageEdit).mockReset();
  vi.mocked(savePageBody).mockReset();
  vi.mocked(fetchTaskSummary).mockReset();
  vi.mocked(fetchTaskCards).mockReset();
  vi.mocked(fetchTaskMaterials).mockReset();
  vi.mocked(fetchTaskFrames).mockReset();
  vi.mocked(fetchTaskApproval).mockReset();
  vi.mocked(fetchTaskResult).mockReset();
  vi.mocked(fetchPageEdit).mockResolvedValue(view);
  vi.mocked(fetchTaskSummary).mockResolvedValue(summary);
  vi.mocked(fetchTaskCards).mockResolvedValue({
    view: "cards",
    rev: 5,
    cards: [],
    omitted: 0,
  });
  vi.mocked(fetchTaskMaterials).mockResolvedValue({
    view: "materials",
    rev: 5,
    materials: [],
    omitted: 0,
  });
  vi.mocked(fetchTaskFrames).mockResolvedValue({
    view: "frames",
    rev: 5,
    frames: [],
    omitted: 0,
  });
  vi.mocked(fetchTaskApproval).mockResolvedValue({
    view: "approval",
    rev: 5,
    status: "idle",
    approval: null,
    approvalStale: false,
    request: null,
    requestStale: false,
  });
  vi.mocked(fetchTaskResult).mockResolvedValue({
    view: "result",
    rev: 5,
    status: "idle",
    result: null,
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  useTaskStore.setState({ summary: null, status: "idle" });
});

async function draw(onClose: () => void) {
  await act(async () => {
    root.render(
      <PageEditDialog project="demo" path="wiki/entropy.md" onClose={onClose} />,
    );
  });
  await act(async () => {});
}

function buttonByText(text: string) {
  return [...document.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(text),
  );
}

async function changeBody(text: string) {
  const textarea = document.querySelector<HTMLTextAreaElement>("textarea");
  expect(textarea).toBeTruthy();
  await act(async () => {
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setValue?.call(textarea, text);
    textarea?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("the page editor and the writing task", () => {
  it("saves the person's prose and re-reads the one task", async () => {
    const onClose = vi.fn();
    vi.mocked(savePageBody)
      .mockResolvedValueOnce({
        path: "wiki/entropy.md",
        changed: true,
        diff: "@@ -1 +1 @@\n-old prose\n+the person's prose",
        added: 1,
        removed: 1,
        revision: "rev-2",
      })
      .mockResolvedValue({
        path: "wiki/entropy.md",
        changed: true,
        diff: "",
        added: 0,
        removed: 0,
        revision: "rev-2",
      });

    await draw(onClose);
    await changeBody("the person's prose");

    // The first press previews; nothing is written and the task is not touched.
    const save = buttonByText(i18n.t("docs.page.edit.save"));
    expect(save).toBeTruthy();
    await act(async () => {
      save?.click();
    });
    await act(async () => {});
    expect(vi.mocked(savePageBody)).toHaveBeenCalledWith("demo", {
      path: "wiki/entropy.md",
      body: "the person's prose",
      baseRevision: "rev-1",
      preview: true,
    });
    expect(vi.mocked(fetchTaskSummary)).not.toHaveBeenCalled();

    // The second press writes with the revision the person opened, then the
    // task is read again: the core recorded the human edit there.
    const confirm = buttonByText(i18n.t("docs.page.edit.confirm"));
    expect(confirm).toBeTruthy();
    await act(async () => {
      confirm?.click();
    });
    await act(async () => {});

    expect(vi.mocked(savePageBody)).toHaveBeenLastCalledWith("demo", {
      path: "wiki/entropy.md",
      body: "the person's prose",
      baseRevision: "rev-1",
    });
    expect(vi.mocked(fetchTaskSummary)).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("does not re-read the task when the person only previews", async () => {
    vi.mocked(savePageBody).mockResolvedValue({
      path: "wiki/entropy.md",
      changed: true,
      diff: "@@ -1 +1 @@\n-old prose\n+the person's prose",
      added: 1,
      removed: 1,
      revision: "rev-2",
    });

    await draw(() => {});
    await changeBody("the person's prose");
    await act(async () => {
      buttonByText(i18n.t("docs.page.edit.check"))?.click();
    });
    await act(async () => {});

    expect(vi.mocked(savePageBody)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(savePageBody).mock.calls[0][1]).toMatchObject({
      preview: true,
    });
    expect(vi.mocked(fetchTaskSummary)).not.toHaveBeenCalled();
  });
});
