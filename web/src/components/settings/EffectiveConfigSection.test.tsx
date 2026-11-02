// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { EffectiveConfigSection } from "@/components/settings/EffectiveConfigSection";
import { installSettings, profileSettings } from "@/components/settings/test-fixtures";
import type { InstallSettings } from "@/lib/install-settings";

/**
 * The read-only answer to "what is the core actually using, and why?".
 *
 * Every value names its source, and a key is reported as stored or not: the
 * screen exists to end a guessing game, not to start one.
 */

let host: HTMLDivElement;
let root: Root;

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

beforeEach(() => {
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

const EFFECTIVE: Partial<InstallSettings> = {
  llm: {
    baseUrl: "https://api.example.com/v1",
    apiKeySet: true,
    model: "gpt-4o-mini",
    temperature: 0.2,
    maxTokens: 8192,
    timeoutSeconds: 90,
    configured: true,
  },
  search: {
    provider: "brave",
    apiKeySet: true,
    timeoutSeconds: 30,
    country: "CN",
    searchLang: "zh-hans",
    uiLang: "zh-CN",
    configured: true,
  },
  extractMedia: false,
  locked: ["llm.temperature"],
  revision: 7,
  profiles: [profileSettings({ name: "Work", baseUrl: "https://api.example.com/v1" })],
  activeProfileId: "p_1",
  sources: {
    "llm.baseUrl": "config",
    "llm.model": "config",
    "llm.apiKey": "keychain",
    "llm.temperature": "env",
    "llm.maxTokens": "default",
    "llm.timeoutSeconds": "config",
    "search.provider": "config",
    "search.apiKey": "keychain",
    "search.timeoutSeconds": "default",
    "search.country": "config",
    "search.searchLang": "config",
    "search.uiLang": "default",
    extractMedia: "default",
  },
};

function install(over: Partial<InstallSettings> = {}): InstallSettings {
  return installSettings({ ...EFFECTIVE, ...over });
}

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(<EffectiveConfigSection install={next} />);
  });
}

describe("the effective configuration", () => {
  it("lists the final values the core uses and where each came from", async () => {
    await draw(install());

    const text = host.textContent ?? "";
    expect(text).toContain("Work");
    expect(text).toContain("https://api.example.com/v1");
    expect(text).toContain("gpt-4o-mini");
    expect(text).toContain("0.2");
    expect(text).toContain("8192");
    expect(text).toContain("90");
    expect(text).toContain("Brave");
    expect(text).toContain("CN");
    expect(text).toContain("zh-hans");
    expect(text).toContain("zh-CN");
    // A key is reported as stored, never as text.
    expect(text).toContain("已保存");
    // The four sources the core can name.
    expect(text).toContain("环境变量");
    expect(text).toContain("配置文件");
    expect(text).toContain("凭据存储");
    expect(text).toContain("默认值");
  });

  it("says there is no active connection instead of showing stale values", async () => {
    await draw(
      install({
        llm: {
          baseUrl: "",
          apiKeySet: false,
          model: "",
          temperature: 0.1,
          maxTokens: 4096,
          timeoutSeconds: 120,
          configured: false,
        },
        profiles: [],
        activeProfileId: null,
        sources: {},
      }),
    );

    const text = host.textContent ?? "";
    expect(text).toContain("没有活动连接");
    expect(text).toContain("未设置");
  });
});
