// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { DeepCheckSection } from "@/components/settings/DeepCheckSection";
import { installSettings as install } from "@/components/settings/test-fixtures";
import type { DeepCheckResult, InstallSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  runDeepCheck: vi.fn(),
}));

import { runDeepCheck } from "@/lib/api/settings";

/**
 * The deep check is the explicit, paid action: it says so before running, and
 * reports every capability it tried rather than one overall verdict.
 */

let host: HTMLDivElement;
let root: Root;

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  vi.restoreAllMocks();
});

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(<DeepCheckSection install={next} />);
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

const PASSED: DeepCheckResult = {
  ok: true,
  checks: [
    { name: "toolCalling", ok: true },
    { name: "nativeSearch", ok: true },
  ],
};

describe("the deep check", () => {
  it("warns about cost and says it uses the saved settings", async () => {
    await draw(install());

    expect(host.textContent).toContain("可能产生费用");
    expect(host.textContent).toContain("已保存的设置");
    expect(button("运行深度检查").disabled).toBe(false);
  });

  it("does not run without a configured connection", async () => {
    await draw(install({ llm: { ...install().llm, configured: false } }));

    expect(button("运行深度检查").disabled).toBe(true);
    expect(host.textContent).toContain(
      i18n.t("settings.deepCheck.notConfigured"),
    );
  });

  it("runs on demand and reports each verified capability", async () => {
    vi.mocked(runDeepCheck).mockResolvedValue(PASSED);
    await draw(install());
    await press("运行深度检查");

    expect(runDeepCheck).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("全部检查通过");
    expect(host.textContent).toContain("工具调用");
    expect(host.textContent).toContain("原生搜索");
    expect(host.textContent).toContain("已验证");
  });

  it("names a failure beside the capability that failed and logs the detail", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    vi.mocked(runDeepCheck).mockResolvedValue({
      ok: false,
      checks: [
        { name: "toolCalling", ok: true },
        {
          name: "nativeSearch",
          ok: false,
          code: "provider.nativeRejected",
          detail: "native search HTTP 401: no",
        },
      ],
    });

    await draw(install());
    await press("运行深度检查");

    expect(host.textContent).toContain("有检查未通过");
    expect(host.textContent).toContain("原生搜索");
    expect(host.textContent).toContain("模型服务拒绝了这次原生搜索");
    expect(host.textContent).toContain("工具调用");
    expect(host.textContent).toContain("已验证");
    expect(info).toHaveBeenCalledWith(
      expect.stringContaining("native search HTTP 401: no"),
    );
  });

  it("reports a request that never completed", async () => {
    vi.mocked(runDeepCheck).mockRejectedValue(new Error("offline"));
    await draw(install());
    await press("运行深度检查");

    expect(host.textContent).toContain("深度检查没有完成");
  });

  it("forgets the result when the saved configuration changes", async () => {
    vi.mocked(runDeepCheck).mockResolvedValue(PASSED);
    await draw(install());
    await press("运行深度检查");
    expect(host.textContent).toContain("全部检查通过");

    await draw(install({ revision: 4 }));
    expect(host.textContent).not.toContain("全部检查通过");
  });

  it("drops an answer that arrives after the configuration changed", async () => {
    let resolveCheck: (value: DeepCheckResult) => void = () => {};
    vi.mocked(runDeepCheck).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCheck = resolve;
      }),
    );

    await draw(install());
    await press("运行深度检查");
    await draw(install({ revision: 4 }));
    await act(async () => {
      resolveCheck(PASSED);
    });

    expect(host.textContent).not.toContain("全部检查通过");
  });
});
