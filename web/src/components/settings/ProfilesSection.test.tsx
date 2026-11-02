// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ProfilesSection } from "@/components/settings/ProfilesSection";
import { ApiError } from "@/lib/api/client";
import type { InstallSettings, ProfileSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  activateProfile: vi.fn(),
}));

import {
  activateProfile,
  createProfile,
  deleteProfile,
  updateProfile,
} from "@/lib/api/settings";

/**
 * Managing the named model connections: the list, the form, and what happens
 * when the document was written by someone else while this draft was open.
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

function profile(over: Partial<ProfileSettings> = {}): ProfileSettings {
  return {
    id: "p_1",
    name: "Main",
    provider: "openai",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
    active: true,
    apiKeySet: true,
    ...over,
  };
}

function install(over: Partial<InstallSettings> = {}): InstallSettings {
  const profiles = over.profiles ?? [];
  return {
    version: "0.1.0",
    llm: {
      baseUrl: "",
      apiKeySet: false,
      model: "",
      temperature: 0.1,
      maxTokens: 4096,
      timeoutSeconds: 120,
      configured: false,
    },
    search: {
      provider: "none",
      apiKeySet: false,
      timeoutSeconds: 30,
      country: "",
      searchLang: "",
      uiLang: "",
      configured: false,
    },
    extractMedia: true,
    locked: [],
    invalid: [],
    revision: 1,
    profiles,
    activeProfileId: profiles.find((item) => item.active)?.id ?? null,
    keychainAvailable: true,
    ...over,
  };
}

async function draw(next: InstallSettings) {
  await act(async () => {
    root.render(
      <ProfilesSection
        install={next}
        onSaved={onSaved}
        onDirty={onDirty}
        onReload={onReload}
      />,
    );
  });
}

async function rerender(next: InstallSettings) {
  await draw(next);
}

function input(label: string): HTMLInputElement {
  const found = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!found) throw new Error(`no input: ${label}`);
  return found;
}

function select(label: string): HTMLSelectElement {
  const found = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`);
  if (!found) throw new Error(`no select: ${label}`);
  return found;
}

function button(name: string): HTMLButtonElement {
  const found = [...host.querySelectorAll("button")].find(
    (item) => item.getAttribute("aria-label") === name || item.textContent?.trim() === name,
  );
  if (!found) throw new Error(`no button: ${name}`);
  return found;
}

function profileButton(name: string): HTMLButtonElement {
  const list = host.querySelector('[aria-label="连接列表"]');
  const found = [...(list?.querySelectorAll("button") ?? [])].find((item) =>
    item.textContent?.includes(name),
  );
  if (!found) throw new Error(`no profile button: ${name}`);
  return found;
}

/** Types into a controlled input the way a reader would. */
function type(element: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
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

async function pressProfile(name: string) {
  await act(async () => {
    profileButton(name).click();
  });
}

describe("a first run with no profiles", () => {
  it("shows an empty state that opens the form in place", async () => {
    await draw(install({ profiles: [], activeProfileId: null, revision: 0 }));

    expect(host.textContent).toContain("还没有保存的连接");
    expect(host.querySelector("input")).toBeNull();

    await press("新建连接");
    expect(input("连接名称")).toBeTruthy();
    expect(host.textContent).toContain("新建连接");
  });

  it("creates the first profile and reports it as active", async () => {
    const created = install({
      profiles: [
        profile({
          id: "p_1",
          name: "DeepSeek",
          provider: "deepseek",
          baseUrl: "https://api.deepseek.com/v1",
          model: "deepseek-chat",
          active: true,
        }),
      ],
      revision: 1,
    });
    vi.mocked(createProfile).mockResolvedValue(created);

    await draw(install({ profiles: [], activeProfileId: null, revision: 0 }));
    await press("新建连接");
    type(input("连接名称"), "DeepSeek");
    choose(select("服务商"), "deepseek");
    type(input("服务地址"), "https://api.deepseek.com/v1");
    type(input("模型名"), "deepseek-chat");
    type(input("密钥"), "sk-secret");
    await press("保存");

    expect(createProfile).toHaveBeenCalledWith({
      name: "DeepSeek",
      provider: "deepseek",
      baseUrl: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      apiKey: "sk-secret",
      revision: 0,
    });
    expect(onSaved).toHaveBeenCalledWith(created);

    await rerender(created);
    expect(host.textContent).toContain("DeepSeek");
    expect(host.textContent).toContain("使用中");
  });
});

describe("the saved connections", () => {
  it("lists every profile and marks the active one", async () => {
    await draw(
      install({
        profiles: [
          profile({ id: "p_1", name: "One", active: false }),
          profile({ id: "p_2", name: "Two", active: true }),
        ],
        revision: 2,
      }),
    );

    expect(host.textContent).toContain("One");
    expect(host.textContent).toContain("Two");
    expect(host.textContent).toContain("2 个连接");
    expect(host.textContent).toContain("使用中");
    // The editor opens on the profile every task is using.
    expect(input("连接名称").value).toBe("Two");
    expect(host.textContent).toContain("当前连接");
  });

  it("saves only the field that changed, and keeps the stored key", async () => {
    const main = profile({ model: "gpt-4o-mini" });
    const changed = install({
      profiles: [{ ...main, model: "gpt-4.1" }],
      revision: 4,
    });
    vi.mocked(updateProfile).mockResolvedValue(changed);

    await draw(install({ profiles: [main], revision: 3 }));
    type(input("模型名"), "gpt-4.1");
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await press("保存");

    expect(updateProfile).toHaveBeenCalledWith("p_1", { model: "gpt-4.1", revision: 3 });
    expect(onSaved).toHaveBeenCalledWith(changed);
  });

  it("switches the active profile and says so at once", async () => {
    const one = profile({ id: "p_1", name: "One", active: false });
    const two = profile({ id: "p_2", name: "Two", active: true });
    const switched = install({
      profiles: [
        { ...one, active: true },
        { ...two, active: false },
      ],
      activeProfileId: "p_1",
      revision: 3,
    });
    vi.mocked(activateProfile).mockResolvedValue(switched);

    await draw(install({ profiles: [one, two], activeProfileId: "p_2", revision: 2 }));
    await pressProfile("One");
    await press("设为当前");

    expect(activateProfile).toHaveBeenCalledWith("p_1", 2);
    expect(onSaved).toHaveBeenCalledWith(switched);

    await rerender(switched);
    expect(host.textContent).toContain("当前连接");
    expect(input("连接名称").value).toBe("One");
  });

  it("deletes the active profile, then the last one, returning to the empty state", async () => {
    const one = profile({ id: "p_1", name: "One", active: true });
    const two = profile({ id: "p_2", name: "Two", active: false });
    const left = install({
      profiles: [{ ...two, active: true }],
      activeProfileId: "p_2",
      revision: 3,
    });
    const empty = install({ profiles: [], activeProfileId: null, revision: 4 });
    vi.mocked(deleteProfile).mockResolvedValueOnce(left).mockResolvedValueOnce(empty);

    await draw(install({ profiles: [one, two], revision: 2 }));
    await press("删除连接");
    expect(host.textContent).toContain("删除这个连接？");
    await press("删除");

    expect(deleteProfile).toHaveBeenCalledWith("p_1", 2);
    await rerender(left);
    expect(host.textContent).not.toContain("One");
    expect(host.textContent).toContain("Two");
    expect(input("连接名称").value).toBe("Two");

    await press("删除连接");
    await press("删除");
    expect(deleteProfile).toHaveBeenLastCalledWith("p_2", 3);
    await rerender(empty);
    expect(host.textContent).toContain("还没有保存的连接");
  });
});

describe("a save the document no longer accepts", () => {
  it("offers a refresh and keeps what was typed", async () => {
    const main = profile();
    vi.mocked(updateProfile).mockRejectedValue(
      new ApiError("stale", 409, "settings.staleRevision"),
    );

    await draw(install({ profiles: [main], revision: 3 }));
    type(input("连接名称"), "Renamed");
    await press("保存");

    expect(host.textContent).toContain("设置已在别处被修改");
    expect(input("连接名称").value).toBe("Renamed");
    expect(onReload).not.toHaveBeenCalled();

    await press("刷新设置");
    expect(onReload).toHaveBeenCalledTimes(1);
    // A refreshed document arrives with a newer revision; the draft stays.
    await rerender(install({ profiles: [main], revision: 4 }));
    expect(input("连接名称").value).toBe("Renamed");
  });

  it("says the same when the first profile could not be saved", async () => {
    vi.mocked(createProfile).mockRejectedValue(
      new ApiError("stale", 409, "settings.staleRevision"),
    );

    await draw(install({ profiles: [], activeProfileId: null, revision: 0 }));
    await press("新建连接");
    type(input("连接名称"), "Main");
    type(input("服务地址"), "https://api.openai.com/v1");
    type(input("模型名"), "gpt-4o-mini");
    await press("保存");

    expect(host.textContent).toContain("设置已在别处被修改");
    expect(input("连接名称").value).toBe("Main");

    await press("刷新设置");
    expect(onReload).toHaveBeenCalledTimes(1);
    await rerender(install({ profiles: [], activeProfileId: null, revision: 1 }));
    expect(input("连接名称").value).toBe("Main");
  });
});
