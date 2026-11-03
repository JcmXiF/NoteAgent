// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { GenerationSection } from "@/components/settings/GenerationSection";
import {
  installSettings as install,
  typeInto,
} from "@/components/settings/test-fixtures";
import type { InstallSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  saveSettings: vi.fn(),
  probeModel: vi.fn(),
}));

import { probeModel, saveSettings } from "@/lib/api/settings";

/**
 * Generation is one answer for every connection. The screen edits it once and
 * says when a value comes from the environment instead of the document.
 */

let host: HTMLDivElement;
let root: Root;
const onSaved = vi.fn();
const onDirty = vi.fn();
const onReload = vi.fn();

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

beforeEach(() => {
  vi.clearAllMocks();
  onReload.mockResolvedValue(undefined);
  vi.mocked(probeModel).mockResolvedValue({ ok: true });
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

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(
      <GenerationSection
        install={next}
        onSaved={onSaved}
        onDirty={onDirty}
        onReload={onReload}
      />,
    );
  });
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

const TEMPERATURE = () => i18n.t("settings.model.temperature");

describe("the global generation values", () => {
  it("shows what every connection uses, with save disabled until something changes", async () => {
    await draw(install());

    expect(input(TEMPERATURE()).value).toBe("0.1");
    expect(input(i18n.t("settings.model.maxTokens")).value).toBe("4096");
    expect(input(i18n.t("settings.model.timeout")).value).toBe("120");
    expect(button("保存").disabled).toBe(true);
    expect(onDirty).toHaveBeenLastCalledWith(false);
  });

  it("saves only the changed parameter, then tests the saved connection", async () => {
    const next = install({
      llm: { ...install().llm, temperature: 0.4 },
      revision: 4,
    });
    vi.mocked(saveSettings).mockResolvedValue(next);

    await draw(install());
    typeInto(input(TEMPERATURE()), "0.4");
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await press("保存");

    expect(saveSettings).toHaveBeenCalledWith({
      llm: { temperature: 0.4 },
      revision: 3,
    });
    expect(onSaved).toHaveBeenCalledWith(next);
    expect(probeModel).toHaveBeenCalledWith({});
    expect(host.textContent).toContain("连接成功");
  });

  it("forgets the test result as soon as a parameter changes", async () => {
    vi.mocked(saveSettings).mockResolvedValue(
      install({ llm: { ...install().llm, temperature: 0.4 }, revision: 4 }),
    );

    await draw(install());
    typeInto(input(TEMPERATURE()), "0.4");
    await press("保存");
    expect(host.textContent).toContain("连接成功");

    typeInto(input(TEMPERATURE()), "0.5");
    expect(host.textContent).not.toContain("连接成功");
  });

  it("tests the draft without saving it", async () => {
    await draw(install());
    typeInto(input(TEMPERATURE()), "0.3");
    await press("测试连接");

    expect(probeModel).toHaveBeenCalledWith({ temperature: 0.3 });
    expect(saveSettings).not.toHaveBeenCalled();
    expect(host.textContent).toContain("连接成功");
  });

  it("drops a result that arrives after the draft changed", async () => {
    let resolveProbe: (value: { ok: true }) => void = () => {};
    vi.mocked(probeModel).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProbe = resolve;
      }),
    );
    vi.mocked(saveSettings).mockResolvedValue(
      install({ llm: { ...install().llm, temperature: 0.4 }, revision: 4 }),
    );

    await draw(install());
    typeInto(input(TEMPERATURE()), "0.4");
    await press("保存");
    // The probe is still running when the reader changes the value again.
    typeInto(input(TEMPERATURE()), "0.5");
    await act(async () => {
      resolveProbe({ ok: true });
    });

    expect(host.textContent).not.toContain("连接成功");
  });

  it("does not test when no connection is configured", async () => {
    const bare = install({
      llm: { ...install().llm, apiKeySet: false, configured: false },
    });
    vi.mocked(saveSettings).mockResolvedValue({
      ...bare,
      llm: { ...bare.llm, temperature: 0.4 },
      revision: 4,
    });

    await draw(bare);
    expect(button("测试连接").disabled).toBe(true);
    typeInto(input(TEMPERATURE()), "0.4");
    await press("保存");

    expect(probeModel).not.toHaveBeenCalled();
    expect(host.textContent).toContain("先保存一个可用的模型连接");
  });
});

describe("a value the environment owns", () => {
  it("is read-only and carries its source", async () => {
    await draw(
      install({
        llm: { ...install().llm, temperature: 0.5 },
        locked: ["llm.temperature"],
        sources: { "llm.temperature": "env" },
      }),
    );

    expect(input(TEMPERATURE()).disabled).toBe(true);
    expect(input(TEMPERATURE()).value).toBe("0.5");
    expect(host.textContent).toContain("环境变量");
  });
});
