import { describe, expect, it } from "vitest";
import {
  generationDirty,
  generationDraftFrom,
  generationPatch,
  generationProblems,
  profileCreateDirty,
  profileCreatePatch,
  profileDirty,
  profileDraftFrom,
  profilePatch,
  profileProblems,
  searchCanProbe,
  searchDirty,
  searchDraftFrom,
  searchPatch,
  searchProblems,
  valueSource,
  type InstallSettings,
  type ProfileSettings,
} from "@/lib/install-settings";

const SAVED: InstallSettings = {
  version: "0.1.0",
  llm: {
    baseUrl: "https://example.com/v1",
    apiKeySet: true,
    model: "demo",
    temperature: 0.1,
    maxTokens: 4096,
    timeoutSeconds: 120,
    configured: true,
  },
  search: {
    provider: "brave",
    mode: "auto",
    nativeAvailable: false,
    apiKeySet: true,
    timeoutSeconds: 30,
    country: "CN",
    searchLang: "zh-hans",
    uiLang: "zh-CN",
    configured: true,
  },
  extractMedia: true,
  locked: [],
  invalid: [],
  revision: 1,
  profiles: [],
  activeProfileId: null,
  keychainAvailable: true,
  sources: {},
  configPath: "C:\\Users\\me\\AppData\\Roaming\\NoteAgent\\config.json",
  schemaVersion: 1,
};

const PROFILE: ProfileSettings = {
  id: "p_1",
  name: "Main",
  provider: "openai",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4o-mini",
  active: true,
  apiKeySet: true,
};

describe("a value's source", () => {
  it("keeps a source the core named", () => {
    expect(valueSource({ "llm.model": "env" }, "llm.model")).toBe("env");
    expect(valueSource({ "llm.model": "config" }, "llm.model")).toBe("config");
    expect(valueSource({ "llm.model": "keychain" }, "llm.model")).toBe("keychain");
  });

  it("falls back to the default for an unnamed or unknown source", () => {
    expect(valueSource({}, "llm.model")).toBe("default");
    expect(valueSource({ "llm.model": "somethingNew" }, "llm.model")).toBe("default");
  });

  it("does not lend one field's source to another", () => {
    expect(valueSource({ "llm.model": "env" }, "llm.temperature")).toBe("default");
  });
});

const GENERATION = { temperature: 0.1, maxTokens: 4096, timeoutSeconds: 120 };

describe("the generation form", () => {
  it("does not treat 0.10 as a different temperature", () => {
    const draft = { ...generationDraftFrom(GENERATION), temperature: "0.10" };
    expect(generationDirty(draft, GENERATION)).toBe(false);
    expect(generationPatch(draft, GENERATION)).toBeNull();
  });

  it("sends only the parameter that changed", () => {
    const draft = { ...generationDraftFrom(GENERATION), maxTokens: "8192" };
    expect(generationPatch(draft, GENERATION)).toEqual({ maxTokens: 8192 });
  });

  it("refuses values outside the ranges the core enforces", () => {
    const draft = { temperature: "3", maxTokens: "0", timeoutSeconds: "1" };
    expect(generationProblems(draft)).toEqual([
      "settings.invalid.temperature",
      "settings.invalid.maxTokens",
      "settings.invalid.timeout",
    ]);
  });
});

describe("the connection form", () => {
  it("treats a blank key as unchanged and sends nothing on its own", () => {
    const draft = profileDraftFrom(PROFILE);
    expect(profileDirty(draft, PROFILE)).toBe(false);
    expect(profilePatch(draft, PROFILE, 3)).toEqual({ revision: 3 });
  });

  it("sends only the field that changed, and a typed key", () => {
    const draft = { ...profileDraftFrom(PROFILE), model: "other", apiKey: "new-key" };
    expect(profilePatch(draft, PROFILE, 4)).toEqual({
      revision: 4,
      model: "other",
      apiKey: "new-key",
    });
  });

  it("clears a key only when asked", () => {
    const draft = { ...profileDraftFrom(PROFILE), clearApiKey: true };
    expect(profilePatch(draft, PROFILE, 5)).toEqual({ revision: 5, clearApiKey: true });
  });

  it("does not treat a trailing slash as a change", () => {
    const draft = { ...profileDraftFrom(PROFILE), baseUrl: "https://api.openai.com/v1/" };
    expect(profileDirty(draft, PROFILE)).toBe(false);
  });

  it("names what the core would refuse", () => {
    const draft = { ...profileDraftFrom(PROFILE), name: " ", baseUrl: "notaurl" };
    expect(profileProblems(draft)).toEqual([
      "settings.invalid.name",
      "settings.invalid.baseUrl",
    ]);
  });

  it("builds a create payload without a blank key", () => {
    const draft = { ...profileDraftFrom(PROFILE), name: "  Main  ", apiKey: "" };
    expect(profileCreateDirty({ ...draft, name: "", baseUrl: "", model: "" })).toBe(false);
    expect(profileCreatePatch(draft, 0)).toEqual({
      name: "Main",
      provider: "openai",
      baseUrl: "https://api.openai.com/v1",
      model: "gpt-4o-mini",
      revision: 0,
    });
  });
});

describe("the search form", () => {
  it("does not resend a country that differs only in case", () => {
    const draft = { ...searchDraftFrom(SAVED.search, true), country: "cn" };
    expect(searchDirty(draft, SAVED.search, true)).toBe(false);
  });

  it("lets search be turned off even when a hidden field is unfinished", () => {
    const draft = {
      ...searchDraftFrom(SAVED.search, true),
      provider: "none" as const,
      country: "not-a-region",
    };
    expect(searchProblems(draft)).toEqual([]);
    expect(searchPatch(draft, SAVED.search, true)?.search?.provider).toBe("none");
  });

  it("will not trial a search that is turned off", () => {
    const draft = { ...searchDraftFrom(SAVED.search, true), provider: "none" as const };
    expect(searchCanProbe(draft, SAVED.search)).toBe(false);
    expect(searchPatch(draft, SAVED.search, true)).toEqual({ search: { provider: "none" } });
  });

  it("saves the image choice on its own", () => {
    const draft = { ...searchDraftFrom(SAVED.search, true), extractMedia: false };
    expect(searchPatch(draft, SAVED.search, true)).toEqual({ extractMedia: false });
  });

  it("saves a changed search mode and nothing else", () => {
    const draft = { ...searchDraftFrom(SAVED.search, true), mode: "external" as const };
    expect(searchDirty(draft, SAVED.search, true)).toBe(true);
    expect(searchPatch(draft, SAVED.search, true)).toEqual({
      search: { mode: "external" },
    });
  });

  it("trials native search only when the core reports an adapter", () => {
    const draft = { ...searchDraftFrom(SAVED.search, true), mode: "native" as const };
    expect(searchCanProbe(draft, { ...SAVED.search, nativeAvailable: false })).toBe(false);
    expect(searchCanProbe(draft, { ...SAVED.search, nativeAvailable: true })).toBe(true);
  });

  it("trials auto with a native adapter even when no external provider is set", () => {
    const draft = {
      ...searchDraftFrom(SAVED.search, true),
      provider: "none" as const,
      mode: "auto" as const,
    };
    expect(searchCanProbe(draft, { ...SAVED.search, nativeAvailable: false })).toBe(false);
    expect(searchCanProbe(draft, { ...SAVED.search, nativeAvailable: true })).toBe(true);
  });
});
