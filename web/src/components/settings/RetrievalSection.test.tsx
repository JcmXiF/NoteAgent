// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { RetrievalSection } from "@/components/settings/RetrievalSection";
import { installSettings, typeInto } from "@/components/settings/test-fixtures";
import type { InstallSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  saveSettings: vi.fn(),
}));

import { saveSettings } from "@/lib/api/settings";

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
