// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { DiagnosticsSection } from "@/components/settings/DiagnosticsSection";
import type { Diagnostics } from "@/lib/api/diagnostics";

vi.mock("@/lib/api/diagnostics", () => ({
  fetchDiagnostics: vi.fn(),
  fetchDiagnosticsLog: vi.fn(),
  checkForUpdate: vi.fn(),
}));

vi.mock("@/lib/browse", () => ({
  openInThisBrowser: vi.fn(),
}));

import { checkForUpdate, fetchDiagnostics, fetchDiagnosticsLog } from "@/lib/api/diagnostics";
import { openInThisBrowser } from "@/lib/browse";

/**
 * The screen a person opens when the app is already misbehaving.
 *
 * What it must do is say what is true and nothing else: the figures it was given,
 * a plain sentence for each thing worth acting on, and no request leaving the
 * machine unless the update button was pressed. The rest is phrasing, which is
 * pinned to one language here because the wording is part of what is checked.
 */

let host: HTMLDivElement;
let root: Root;
const onOpenSettings = vi.fn();

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

beforeEach(() => {
  vi.clearAllMocks();
  // A floor for every test: the report reads, and the two actions the screen
  // offers answer without a source. A test that cares overrides what it needs.
  vi.mocked(fetchDiagnostics).mockResolvedValue(report());
  vi.mocked(fetchDiagnosticsLog).mockResolvedValue({ path: null, lines: [] });
  vi.mocked(checkForUpdate).mockResolvedValue({ configured: false, current: "0.1.0" });
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

function report(over: Partial<Diagnostics> = {}): Diagnostics {
  return {
    version: "0.1.0",
    pid: 4242,
    python: "3.12.4",
    platform: "Windows-11",
    startedAt: 0,
    uptimeSeconds: 7_200,
    address: {
      host: "127.0.0.1",
      port: 8765,
      requestedPort: 8765,
      fallback: false,
      url: "http://127.0.0.1:8765",
    },
    dataRoot: "C:\\notes",
    projectsRoot: "C:\\notes\\projects",
    projectCount: 2,
    activeProject: "alpha",
    logs: null,
    supervised: false,
    restarts: 0,
    lastExit: null,
    warnings: [],
    ...over,
  };
}

async function draw() {
  await act(async () => {
    root.render(<DiagnosticsSection onOpenSettings={onOpenSettings} />);
  });
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

describe("what this copy of the app says about itself", () => {
  it("shows the version, how long it has been running, and where the notes are", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    await draw();

    const text = host.textContent ?? "";
    expect(text).toContain("0.1.0");
    expect(text).toContain("2 小时 0 分");
    expect(text).toContain("http://127.0.0.1:8765");
    expect(text).toContain("2 个，当前是「alpha」");
    expect(text).toContain("C:\\notes");
    expect(text).toContain("未开启");
    // A checkout keeps no log of its own, and the screen says so rather than
    // offering a button that would open nothing.
    expect(text).toContain("此安装不单独保留日志。");
  });

  it("says how often the service was started again, and when it last died", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(
      report({
        supervised: true,
        restarts: 2,
        lastExit: { code: 1, at: 1_700_000_000 },
      }),
    );
    await draw();

    const text = host.textContent ?? "";
    expect(text).toContain("已开启");
    expect(text).toContain("已经重来 2 次");
    expect(text).toMatch(/上次意外停止在 .+代码 1/);
  });

  it("names what is worth acting on, and points at the setting", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(
      report({ warnings: ["noProject", "noModel", "somethingThisBuildDoesNotKnow"] }),
    );
    await draw();

    const text = host.textContent ?? "";
    expect(text).toContain("尚未创建项目");
    expect(text).toContain("尚未配置模型服务");
    expect(text).not.toContain("somethingThisBuildDoesNotKnow");

    await press("去配置模型");
    expect(onOpenSettings).toHaveBeenCalledWith("model");
  });

  it("shows the end of the log once, and hides it again without asking twice", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(
      report({ logs: { path: "C:\\logs\\core.log", exists: true, bytes: 120 } }),
    );
    vi.mocked(fetchDiagnosticsLog).mockResolvedValue({
      path: "C:\\logs\\core.log",
      lines: ["starting", "serving on 8765"],
    });
    await draw();

    await press("看最近的日志");
    expect(host.querySelector("pre")?.textContent).toContain("serving on 8765");

    await press("收起日志");
    expect(host.querySelector("pre")).toBeNull();
    expect(fetchDiagnosticsLog).toHaveBeenCalledTimes(1);

    await press("看最近的日志");
    expect(host.querySelector("pre")?.textContent).toContain("serving on 8765");
    expect(fetchDiagnosticsLog).toHaveBeenCalledTimes(1);
  });

  it("keeps the reading still when the log was empty", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(
      report({ logs: { path: "C:\\logs\\core.log", exists: true, bytes: 0 } }),
    );
    vi.mocked(fetchDiagnosticsLog).mockResolvedValue({ path: "C:\\logs\\core.log", lines: [] });
    await draw();

    await press("看最近的日志");
    expect(host.textContent).toContain("日志目前为空。");
  });
});

describe("the update question", () => {
  it("is not asked until the button is pressed", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    await draw();

    expect(checkForUpdate).not.toHaveBeenCalled();
    await press("检查更新");
    expect(checkForUpdate).toHaveBeenCalledTimes(1);
  });

  it("says plainly when this installation has no source to ask", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    vi.mocked(checkForUpdate).mockResolvedValue({ configured: false, current: "0.1.0" });
    await draw();

    await press("检查更新");
    expect(host.textContent).toContain("此安装未配置更新来源。当前版本为 0.1.0。");
  });

  it("shows the newer version with its notes, and opens the download page", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    vi.mocked(checkForUpdate).mockResolvedValue({
      configured: true,
      current: "0.1.0",
      latest: "0.2.0",
      available: true,
      notes: "Saves are faster.",
      url: "https://example.test/download",
    });
    await draw();

    await press("检查更新");
    expect(host.textContent).toContain("可用新版本 0.2.0，当前为 0.1.0。");
    expect(host.textContent).toContain("Saves are faster.");

    await press("打开下载页");
    expect(openInThisBrowser).toHaveBeenCalledWith("https://example.test/download");
  });

  it("says the version is current when nothing newer exists", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    vi.mocked(checkForUpdate).mockResolvedValue({
      configured: true,
      current: "0.1.0",
      latest: "0.1.0",
      available: false,
      notes: "",
      url: "",
    });
    await draw();

    await press("检查更新");
    expect(host.textContent).toContain("当前已是最新版本（0.1.0）。");
    expect(host.textContent).not.toContain("打开下载页");
  });

  it("words a failure the reader can act on", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    vi.mocked(checkForUpdate).mockRejectedValue(new Error("socket closed"));
    await draw();

    await press("检查更新");
    expect(host.textContent).toContain("检查更新未完成。");
  });
});

describe("when the report itself cannot be read", () => {
  it("says so, and reads again on request", async () => {
    vi.mocked(fetchDiagnostics).mockRejectedValueOnce(new Error("not there"));
    await draw();

    expect(host.textContent).toContain("这些信息没有读出来。");

    vi.mocked(fetchDiagnostics).mockResolvedValue(report());
    await press("再试一次");

    expect(host.textContent).toContain("0.1.0");
    expect(fetchDiagnostics).toHaveBeenCalledTimes(2);
  });
});
