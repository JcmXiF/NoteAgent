// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { RetrievalSection } from "@/components/settings/RetrievalSection";
import { installSettings, typeInto } from "@/components/settings/test-fixtures";
import type { InstallSettings } from "@/lib/install-settings";
import { useWorkspaceStore } from "@/stores/workspace-store";

vi.mock("@/lib/api/settings", () => ({
  saveSettings: vi.fn(),
  fetchRagStatus: vi.fn(),
  rebuildRagIndex: vi.fn(),
}));

import {
  fetchRagStatus,
  rebuildRagIndex,
  saveSettings,
  type RagRebuildReport,
} from "@/lib/api/settings";

/**
 * Retrieval is one install-wide switch. The service fields stay out of the way
 * until it is on, and a switch that is on always names a service it can call.
 */

let host: HTMLDivElement;
let root: Root;
const onSaved = vi.fn();
const onDirty = vi.fn();

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

beforeEach(() => {
  vi.clearAllMocks();
  useWorkspaceStore.setState({ current: null });
  vi.mocked(fetchRagStatus).mockResolvedValue({
    state: "disabled",
    units: 0,
    chunks: 0,
    failed: 0,
    reason: "",
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
});

function install(over: Partial<InstallSettings> = {}): InstallSettings {
  return installSettings({
    rag: {
      enabled: false,
      embedding: { endpoint: "", model: "Qwen3-Embedding-0.6B" },
    },
    ...over,
  });
}

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(<RetrievalSection install={next} onSaved={onSaved} onDirty={onDirty} />);
  });
}

function toggle(): HTMLElement {
  const found = host.querySelector<HTMLElement>('[role="checkbox"]');
  if (!found) throw new Error("no retrieval switch");
  return found;
}

function input(label: string): HTMLInputElement {
  const found = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!found) throw new Error(`no input: ${label}`);
  return found;
}

function button(name: string): HTMLButtonElement {
  const found = [...host.querySelectorAll("button")].find(
    (item) => item.textContent?.trim() === name,
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found;
}

async function press(name: string) {
  await act(async () => {
    button(name).click();
  });
}

async function click(element: HTMLElement) {
  await act(async () => {
    element.click();
  });
}

describe("the retrieval switch", () => {
  it("keeps the service fields hidden while it is off", async () => {
    await draw(install());

    expect(toggle().getAttribute("aria-checked")).toBe("false");
    expect(host.querySelector('input[aria-label="服务地址"]')).toBeNull();
    expect(button("保存").disabled).toBe(true);
  });

  it("asks for a service before it can be switched on", async () => {
    await draw(install());
    await click(toggle());

    expect(host.textContent).toContain("请填写以 http:// 或 https:// 开头的嵌入服务地址。");
    expect(host.textContent).toContain("wiki 内容页会发送到这里的嵌入服务");
    expect(button("保存").disabled).toBe(true);

    act(() => {
      typeInto(input("服务地址"), "http://localhost:8080/v1");
    });
    expect(button("保存").disabled).toBe(false);
  });

  it("drops a service draft once the switch is off again", async () => {
    await draw(install());
    await click(toggle());
    act(() => {
      typeInto(input("服务地址"), "http://localhost:8080/v1");
    });
    expect(onDirty).toHaveBeenLastCalledWith(true);

    await click(toggle());

    expect(onDirty).toHaveBeenLastCalledWith(false);
    expect(button("保存").disabled).toBe(true);
  });

  it("saves the switch with the service it will call", async () => {
    const next = install({
      rag: {
        enabled: true,
        embedding: {
          endpoint: "http://localhost:8080/v1",
          model: "Qwen3-Embedding-0.6B",
        },
      },
      revision: 4,
    });
    vi.mocked(saveSettings).mockResolvedValue(next);

    await draw(install());
    await click(toggle());
    act(() => {
      typeInto(input("服务地址"), "http://localhost:8080/v1");
    });
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await press("保存");

    expect(saveSettings).toHaveBeenCalledWith({
      rag: {
        enabled: true,
        embedding: { endpoint: "http://localhost:8080/v1" },
      },
      revision: 3,
    });
    expect(onSaved).toHaveBeenCalledWith(next);
  });

  it("keeps a switch the environment owns read-only", async () => {
    await draw(
      install({
        rag: {
          enabled: true,
          embedding: {
            endpoint: "http://localhost:8080/v1",
            model: "Qwen3-Embedding-0.6B",
          },
        },
        locked: ["rag.enabled", "rag.embedding.endpoint"],
        sources: { "rag.enabled": "env", "rag.embedding.endpoint": "env" },
      }),
    );

    expect(toggle().hasAttribute("data-disabled")).toBe(true);
    expect(input("服务地址").disabled).toBe(true);
    expect(host.textContent).toContain("环境变量");
  });
});

describe("the index status", () => {
  it("shows the current project's state and counts", async () => {
    vi.mocked(fetchRagStatus).mockResolvedValue({
      state: "ready",
      units: 2,
      chunks: 5,
      failed: 0,
      reason: "",
    });

    await draw(install());

    expect(fetchRagStatus).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("索引就绪");
    expect(host.textContent).toContain("2 个页面");
    expect(host.textContent).toContain("5 个片段");
    expect(button("重建").disabled).toBe(false);
  });

  it("says why the index cannot be read and cannot rebuild it", async () => {
    vi.mocked(fetchRagStatus).mockResolvedValue({
      state: "unavailable",
      units: 0,
      chunks: 0,
      failed: 0,
      reason: "no active project: none",
    });

    await draw(install());

    expect(host.textContent).toContain("无法读取索引");
    expect(host.textContent).toContain("no active project");
    expect(button("重建").disabled).toBe(true);
  });

  it("cannot rebuild while retrieval is off", async () => {
    await draw(install());

    expect(host.textContent).toContain("检索已关闭");
    expect(button("重建").disabled).toBe(true);
  });

  it("reads the status again when the project changes", async () => {
    await draw(install());
    expect(fetchRagStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      useWorkspaceStore.setState({ current: "other" });
    });

    expect(fetchRagStatus).toHaveBeenCalledTimes(2);
  });

  it("does not leave the previous project's index on screen", async () => {
    vi.mocked(fetchRagStatus).mockResolvedValueOnce({
      state: "ready",
      units: 2,
      chunks: 5,
      failed: 0,
      reason: "",
    });
    await draw(install());
    expect(host.textContent).toContain("2 个页面");

    let fail!: (error: Error) => void;
    const pending = new Promise<never>((_resolve, reject) => {
      fail = reject;
    });
    vi.mocked(fetchRagStatus).mockReturnValueOnce(pending);

    await act(async () => {
      useWorkspaceStore.setState({ current: "other" });
    });

    expect(host.textContent).not.toContain("2 个页面");
    expect(host.textContent).toContain("正在读取索引");

    await act(async () => {
      fail(new Error("boom"));
      await pending.catch(() => undefined);
    });

    expect(host.textContent).toContain("无法读取索引");
    expect(host.textContent).not.toContain("2 个页面");
  });

  it("reads the status again when asked", async () => {
    vi.mocked(fetchRagStatus)
      .mockResolvedValueOnce({
        state: "cold",
        units: 1,
        chunks: 0,
        failed: 0,
        reason: "",
      })
      .mockResolvedValue({
        state: "ready",
        units: 1,
        chunks: 3,
        failed: 0,
        reason: "",
      });

    await draw(install());
    expect(host.textContent).toContain("索引尚未建立");

    await press("刷新");

    expect(fetchRagStatus).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("索引就绪");
    expect(host.textContent).toContain("3 个片段");
  });

  it("shows progress and the report while it rebuilds", async () => {
    let finish!: (report: RagRebuildReport) => void;
    const pending = new Promise<RagRebuildReport>((resolve) => {
      finish = resolve;
    });
    vi.mocked(rebuildRagIndex).mockReturnValue(pending);
    vi.mocked(fetchRagStatus)
      .mockResolvedValueOnce({
        state: "cold",
        units: 1,
        chunks: 0,
        failed: 0,
        reason: "",
      })
      .mockResolvedValue({
        state: "ready",
        units: 1,
        chunks: 2,
        failed: 0,
        reason: "",
      });

    await draw(install());
    await press("重建");

    expect(button("重建中…").disabled).toBe(true);
    expect(rebuildRagIndex).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish({
        added: 1,
        updated: 0,
        unchanged: 0,
        deleted: 0,
        embeddedChunks: 2,
        rebuilt: true,
        failures: [],
      });
      await pending;
    });

    expect(host.textContent).toContain("新增 1");
    expect(host.textContent).toContain("嵌入 2 个片段");
    expect(host.textContent).toContain("索引就绪");
  });

  it("names the pages that could not be built", async () => {
    vi.mocked(fetchRagStatus).mockResolvedValueOnce({
      state: "cold",
      units: 1,
      chunks: 0,
      failed: 0,
      reason: "",
    });
    vi.mocked(rebuildRagIndex).mockResolvedValue({
      added: 1,
      updated: 0,
      unchanged: 0,
      deleted: 0,
      embeddedChunks: 0,
      rebuilt: true,
      failures: [{ unitId: "wiki:a", pageId: "a", reason: "down" }],
    });

    await draw(install());
    await press("重建");

    expect(host.textContent).toContain("未能建立的页面");
    expect(host.textContent).toContain("a");
    expect(host.textContent).toContain("down");
  });
});
