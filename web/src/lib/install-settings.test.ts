import { describe, expect, it } from "vitest";
import {
  llmCanProbe,
  llmDirty,
  llmDraftFrom,
  llmPatch,
  llmProblems,
  searchCanProbe,
  searchDirty,
  searchDraftFrom,
  searchPatch,
  searchProblems,
  type InstallSettings,
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
};

describe("the model form", () => {
  it("treats a blank key as unchanged", () => {
    const draft = llmDraftFrom(SAVED.llm);
    expect(llmDirty(draft, SAVED.llm)).toBe(false);
    expect(llmPatch(draft, SAVED.llm)).toBeNull();
    expect(llmCanProbe(draft, SAVED.llm)).toBe(true);
  });

  it("sends only the field that changed, and a typed key", () => {
    const draft = { ...llmDraftFrom(SAVED.llm), model: "other", apiKey: "new-key" };
    expect(llmPatch(draft, SAVED.llm)).toEqual({ model: "other", apiKey: "new-key" });
  });

  it("clears a key only when asked", () => {
    const draft = { ...llmDraftFrom(SAVED.llm), clearApiKey: true };
    expect(llmPatch(draft, SAVED.llm)).toEqual({ clearApiKey: true });
    expect(llmCanProbe(draft, SAVED.llm)).toBe(false);
  });

  it("refuses an address that is not an http(s) URL", () => {
    const draft = { ...llmDraftFrom(SAVED.llm), baseUrl: "notaurl" };
    expect(llmProblems(draft)).toContain("settings.invalid.baseUrl");
    expect(llmCanProbe(draft, SAVED.llm)).toBe(false);
  });

  it("does not treat 0.10 as a different temperature", () => {
    const draft = { ...llmDraftFrom(SAVED.llm), temperature: "0.10" };
    expect(llmDirty(draft, SAVED.llm)).toBe(false);
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
});
