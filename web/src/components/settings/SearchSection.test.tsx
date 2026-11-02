// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SearchSection } from "@/components/settings/SearchSection";
import { installSettings } from "@/components/settings/test-fixtures";
import type { InstallSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  saveSettings: vi.fn(),
  probeSearch: vi.fn(),
}));

import { probeSearch, saveSettings } from "@/lib/api/settings";

/**
 * The search settings share the connection's configuration surface. The mode
 * chooses which path a search takes; native has no adapter in this build yet.
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
  vi.mocked(probeSearch).mockResolvedValue({ ok: true, count: 1 });
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
    search: {
      provider: "brave",
      mode: "auto",
      nativeAvailable: false,
      apiKeySet: true,
      timeoutSeconds: 30,
      country: "",
      searchLang: "",
      uiLang: "",
      configured: true,
    },
    ...over,
  });
}

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(<SearchSection install={next} onSaved={onSaved} onDirty={onDirty} />);
  });
}

function select(label: string): HTMLSelectElement {
  const found = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`);
  if (!found) throw new Error(`no select: ${label}`);
  return found;
}

function button(name: string): HTMLButtonElement {
  const found = [...host.querySelectorAll("button")].find(
    (item) => item.textContent?.trim() === name,
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found;
}

function choose(element: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

async function press(name: string) {
  await act(async () => {
    button(name).click();
  });
}

describe("the search mode", () => {
  it("disables native search until a model service supports it", async () => {
    await draw(install({ search: { ...install().search, mode: "native" } }));

    const mode = select("搜索方式");
    const native = [...mode.options].find((option) => option.value === "native");
    expect(native?.disabled).toBe(true);
    expect(button("试搜一次").disabled).toBe(true);
    expect(host.textContent).toContain("当前版本尚未提供原生搜索");
  });

  it("saves a changed mode with the shared configuration", async () => {
    const next = install({
      search: { ...install().search, mode: "external" },
      revision: 4,
    });
    vi.mocked(saveSettings).mockResolvedValue(next);

    await draw(install());
    choose(select("搜索方式"), "external");
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await press("保存");

    expect(saveSettings).toHaveBeenCalledWith({
      search: { mode: "external" },
      revision: 3,
    });
    expect(onSaved).toHaveBeenCalledWith(next);
  });

  it("keeps a mode the environment owns read-only", async () => {
    await draw(
      install({
        search: { ...install().search, mode: "external" },
        locked: ["search.mode"],
        sources: { "search.mode": "env" },
      }),
    );

    expect(select("搜索方式").disabled).toBe(true);
    expect(host.textContent).toContain("环境变量");
  });
});
