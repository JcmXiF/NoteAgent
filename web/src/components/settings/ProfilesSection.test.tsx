// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ProfilesSection } from "@/components/settings/ProfilesSection";
import {
  installSettings as install,
  profileSettings as profile,
  typeInto,
} from "@/components/settings/test-fixtures";
import { ApiError } from "@/lib/api/client";
import type { InstallSettings } from "@/lib/install-settings";

vi.mock("@/lib/api/settings", () => ({
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  activateProfile: vi.fn(),
  listModels: vi.fn(),
}));

import {
  activateProfile,
  createProfile,
  deleteProfile,
  listModels,
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
    typeInto(input("连接名称"), "DeepSeek");
    choose(select("服务商"), "deepseek");
    typeInto(input("服务地址"), "https://api.deepseek.com/v1");
    typeInto(input("模型名"), "deepseek-chat");
    typeInto(input("密钥"), "sk-secret");
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
    typeInto(input("模型名"), "gpt-4.1");
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await press("保存");

    expect(updateProfile).toHaveBeenCalledWith("p_1", { model: "gpt-4.1", revision: 3 });
    expect(onSaved).toHaveBeenCalledWith(changed);
  });

  it("fills the endpoint and a suggested model from a preset, and still takes a typed one", async () => {
    await draw(
      install({
        profiles: [
          profile({ provider: "custom", baseUrl: "https://old.example/v1", model: "old" }),
        ],
        revision: 2,
      }),
    );

    choose(select("服务商"), "deepseek");
    expect(input("服务地址").value).toBe("https://api.deepseek.com/v1");
    expect(input("模型名").value).toBe("deepseek-chat");

    typeInto(input("模型名"), "deepseek-reasoner");
    expect(input("模型名").value).toBe("deepseek-reasoner");
  });

  it("lets a new connection use a preset when the active one is environment-owned", async () => {
    await draw(
      install({
        profiles: [profile()],
        locked: ["llm.baseUrl", "llm.model"],
        sources: { "llm.baseUrl": "env", "llm.model": "env" },
        revision: 2,
      }),
    );

    await press("新建");
    choose(select("服务商"), "deepseek");

    expect(input("服务地址").disabled).toBe(false);
    expect(input("服务地址").value).toBe("https://api.deepseek.com/v1");
    expect(input("模型名").disabled).toBe(false);
    expect(input("模型名").value).toBe("deepseek-chat");
  });

  it("shows a fetched model list as suggestions and fills one on click", async () => {
    vi.mocked(listModels).mockResolvedValue({ models: ["gpt-4.1", "gpt-4o-mini"] });

    await draw(install());
    await press("拉取列表");

    expect(listModels).toHaveBeenCalledWith({
      baseUrl: "https://api.openai.com/v1",
      provider: "openai",
      profileId: "p_1",
    });
    await press("gpt-4.1");
    expect(input("模型名").value).toBe("gpt-4.1");
  });

  it("drops a model list that arrives after the endpoint changed", async () => {
    let resolveModels: (value: { models: string[] }) => void = () => {};
    vi.mocked(listModels).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveModels = resolve;
      }),
    );

    await draw(install());
    await press("拉取列表");
    typeInto(input("服务地址"), "https://other.example/v1");
    await act(async () => {
      resolveModels({ models: ["old-model"] });
    });

    expect(host.textContent).not.toContain("old-model");
  });

  it("keeps the typed model when the list cannot be fetched, and still saves it", async () => {
    vi.mocked(listModels).mockRejectedValue(
      new ApiError("no list", 502, "settings.modelsUnavailable"),
    );

    await draw(install());
    typeInto(input("模型名"), "my-model");
    await press("拉取列表");

    expect(host.textContent).toContain("这个服务没有返回模型列表");
    expect(input("模型名").value).toBe("my-model");

    const saved = install({ profiles: [profile({ model: "my-model" })], revision: 4 });
    vi.mocked(updateProfile).mockResolvedValue(saved);
    await press("保存");
    expect(updateProfile).toHaveBeenCalledWith(
      "p_1",
      expect.objectContaining({ model: "my-model" }),
    );
  });

  it("keeps the active connection's environment-owned fields read-only", async () => {
    await draw(
      install({
        profiles: [
          profile({ id: "p_1", name: "One", active: false }),
          profile({ id: "p_2", name: "Two", active: true }),
        ],
        activeProfileId: "p_2",
        locked: ["llm.baseUrl"],
        sources: { "llm.baseUrl": "env" },
        revision: 2,
      }),
    );

    expect(input("服务地址").disabled).toBe(true);
    expect(host.textContent).toContain("环境变量");

    // Only the connection that is actually in use is fixed by the environment.
    await pressProfile("One");
    expect(input("服务地址").disabled).toBe(false);
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
    typeInto(input("连接名称"), "Renamed");
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
    typeInto(input("连接名称"), "Main");
    typeInto(input("服务地址"), "https://api.openai.com/v1");
    typeInto(input("模型名"), "gpt-4o-mini");
    await press("保存");

    expect(host.textContent).toContain("设置已在别处被修改");
    expect(input("连接名称").value).toBe("Main");

    await press("刷新设置");
    expect(onReload).toHaveBeenCalledTimes(1);
    await rerender(install({ profiles: [], activeProfileId: null, revision: 1 }));
    expect(input("连接名称").value).toBe("Main");
  });
});
