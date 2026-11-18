/**
 * The settings payload the component tests draw.
 *
 * One shape, so adding a field to the answer is one edit rather than one per
 * test file. Tests override only what they are about.
 */

import type {
  InstallSettings,
  ProfileSettings,
  RagEmbeddingSettings,
  RagRerankSettings,
  RagSettings,
} from "@/lib/install-settings";

export function ragSettings(
  over: Partial<Omit<RagSettings, "embedding" | "rerank">> & {
    embedding?: Partial<RagEmbeddingSettings>;
    rerank?: Partial<RagRerankSettings>;
  } = {},
): RagSettings {
  const { embedding, rerank, ...rest } = over;
  return {
    enabled: false,
    embedding: {
      endpoint: "",
      model: "Qwen3-Embedding-0.6B",
      queryPrefix: "Q: {query}",
      batch: 64,
      concurrency: 1,
      apiKeySet: false,
      ...embedding,
    },
    rerank: {
      enabled: false,
      endpoint: "",
      model: "",
      candidates: 50,
      topN: 10,
      apiKeySet: false,
      ...rerank,
    },
    ...rest,
  };
}

export function profileSettings(over: Partial<ProfileSettings> = {}): ProfileSettings {
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

export function installSettings(over: Partial<InstallSettings> = {}): InstallSettings {
  const profiles = over.profiles ?? [profileSettings()];
  return {
    version: "0.1.0",
    llm: {
      baseUrl: "https://api.openai.com/v1",
      apiKeySet: true,
      model: "gpt-4o-mini",
      temperature: 0.1,
      maxTokens: 4096,
      timeoutSeconds: 120,
      configured: true,
    },
    search: {
      provider: "none",
      mode: "auto",
      nativeAvailable: false,
      apiKeySet: false,
      timeoutSeconds: 30,
      country: "",
      searchLang: "",
      uiLang: "",
      configured: false,
    },
    rag: ragSettings(),
    extractMedia: true,
    locked: [],
    invalid: [],
    revision: 3,
    profiles,
    activeProfileId: profiles.find((item) => item.active)?.id ?? null,
    keychainAvailable: true,
    sources: {},
    configPath: "C:\\Users\\me\\AppData\\Roaming\\NoteAgent\\config.json",
    schemaVersion: 1,
    ...over,
  };
}

/** Types into a controlled input the way a reader would. */
export function typeInto(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}
