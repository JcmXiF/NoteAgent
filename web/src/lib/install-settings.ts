/**
 * The install settings form: what is dirty, what is wrong, and what to send.
 *
 * The core owns the ranges and is the one that writes. This module only decides
 * which fields differ from the last answer, so a save does not resend a key the
 * person left blank on purpose — a blank key means "keep the saved one".
 */

export type LlmSettings = {
  baseUrl: string;
  apiKeySet: boolean;
  model: string;
  temperature: number;
  maxTokens: number;
  timeoutSeconds: number;
  configured: boolean;
};

export type SearchProvider = "none" | "brave" | "serper";

/** The canonical ids the core accepts for a connection profile. */
export type ProviderId = "openai" | "anthropic" | "deepseek" | "qwen" | "ollama" | "custom";

export type ProfileSettings = {
  id: string;
  name: string;
  provider: ProviderId;
  baseUrl: string;
  model: string;
  active: boolean;
  /** A key is stored for this profile; the key itself is never sent back. */
  apiKeySet: boolean;
};

export type ProfileDraft = {
  name: string;
  provider: ProviderId;
  baseUrl: string;
  model: string;
  apiKey: string;
  clearApiKey: boolean;
};

export type ProfileCreatePatch = {
  name: string;
  provider: ProviderId;
  baseUrl: string;
  model: string;
  apiKey?: string;
  revision: number;
};

export type ProfileUpdatePatch = {
  revision: number;
  name?: string;
  provider?: ProviderId;
  baseUrl?: string;
  model?: string;
  apiKey?: string;
  clearApiKey?: boolean;
};

export type SearchSettings = {
  provider: SearchProvider;
  apiKeySet: boolean;
  timeoutSeconds: number;
  country: string;
  searchLang: string;
  uiLang: string;
  configured: boolean;
};

export type InstallSettings = {
  version: string;
  llm: LlmSettings;
  search: SearchSettings;
  extractMedia: boolean;
  locked: string[];
  invalid: string[];
  /** The document revision this answer was read from. A save carries it back. */
  revision: number;
  profiles: ProfileSettings[];
  activeProfileId: string | null;
  /** False when the OS credential store cannot be used: keys can be neither read nor saved. */
  keychainAvailable: boolean;
};

export type LlmDraft = {
  baseUrl: string;
  apiKey: string;
  clearApiKey: boolean;
  model: string;
  temperature: string;
  maxTokens: string;
  timeoutSeconds: string;
};

export type SearchDraft = {
  provider: SearchProvider;
  apiKey: string;
  clearApiKey: boolean;
  timeoutSeconds: string;
  country: string;
  searchLang: string;
  uiLang: string;
  extractMedia: boolean;
};

export type SettingsPatch = {
  llm?: Record<string, unknown>;
  search?: Record<string, unknown>;
  extractMedia?: boolean;
  /** The revision the form was built from; a stale one is refused with 409. */
  revision?: number;
};

const LANG = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/;

export function isLocked(locked: readonly string[], field: string): boolean {
  return locked.includes(field);
}

export function llmDraftFrom(llm: LlmSettings): LlmDraft {
  return {
    baseUrl: llm.baseUrl,
    apiKey: "",
    clearApiKey: false,
    model: llm.model,
    temperature: String(llm.temperature),
    maxTokens: String(llm.maxTokens),
    timeoutSeconds: String(llm.timeoutSeconds),
  };
}

export function profileDraftFrom(profile: ProfileSettings): ProfileDraft {
  return {
    name: profile.name,
    provider: profile.provider,
    baseUrl: profile.baseUrl,
    model: profile.model,
    apiKey: "",
    clearApiKey: false,
  };
}

function trimmedBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

/** The fields a draft changed. A blank key is absent, so the saved one stays. */
function changedProfileFields(
  draft: ProfileDraft,
  profile: ProfileSettings,
): Omit<ProfileUpdatePatch, "revision"> {
  const changes: Omit<ProfileUpdatePatch, "revision"> = {};
  if (draft.name.trim() !== profile.name) changes.name = draft.name.trim();
  if (draft.provider !== profile.provider) changes.provider = draft.provider;
  if (trimmedBaseUrl(draft.baseUrl) !== profile.baseUrl) changes.baseUrl = draft.baseUrl.trim();
  if (draft.model.trim() !== profile.model) changes.model = draft.model.trim();
  if (draft.apiKey.trim()) changes.apiKey = draft.apiKey.trim();
  else if (draft.clearApiKey) changes.clearApiKey = true;
  return changes;
}

/** Whether the form holds something worth sending; a saved key is not in it. */
export function profileDirty(draft: ProfileDraft, profile: ProfileSettings): boolean {
  return Object.keys(changedProfileFields(draft, profile)).length > 0;
}

export function profileCreateDirty(draft: ProfileDraft): boolean {
  return Boolean(
    draft.name.trim() || draft.baseUrl.trim() || draft.model.trim() || draft.apiKey.trim(),
  );
}

/** i18n keys for values the core would refuse on a profile save. */
export function profileProblems(draft: ProfileDraft): string[] {
  const problems: string[] = [];
  if (!draft.name.trim()) problems.push("settings.invalid.name");
  if (!validBaseUrl(draft.baseUrl)) problems.push("settings.invalid.baseUrl");
  if (!validSecret(draft.apiKey)) problems.push("settings.invalid.secret");
  return problems;
}

export function profileCreatePatch(
  draft: ProfileDraft,
  revision: number,
): ProfileCreatePatch {
  return {
    name: draft.name.trim(),
    provider: draft.provider,
    baseUrl: draft.baseUrl.trim(),
    model: draft.model.trim(),
    ...(draft.apiKey.trim() ? { apiKey: draft.apiKey.trim() } : {}),
    revision,
  };
}

/** The revision makes a stale save a 409 instead of a silent overwrite. */
export function profilePatch(
  draft: ProfileDraft,
  profile: ProfileSettings,
  revision: number,
): ProfileUpdatePatch {
  return { revision, ...changedProfileFields(draft, profile) };
}

export function searchDraftFrom(search: SearchSettings, extractMedia: boolean): SearchDraft {
  return {
    provider: search.provider,
    apiKey: "",
    clearApiKey: false,
    timeoutSeconds: String(search.timeoutSeconds),
    country: search.country,
    searchLang: search.searchLang,
    uiLang: search.uiLang,
    extractMedia,
  };
}

function validBaseUrl(value: string): boolean {
  const text = value.trim().replace(/\/+$/, "");
  if (!text || /\s/.test(text) || text.length > 500) return false;
  try {
    const url = new URL(text);
    return (url.protocol === "http:" || url.protocol === "https:") && Boolean(url.hostname);
  } catch {
    return false;
  }
}

function validModel(value: string): boolean {
  const text = value.trim();
  return Boolean(text) && text.length <= 200 && !/[\r\n]/.test(text);
}

function validSecret(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  return text.length <= 2000 && !/[\r\n]/.test(value);
}

function numberInRange(value: string, min: number, max: number): boolean {
  const text = value.trim();
  if (!text) return false;
  const number = Number(text);
  return Number.isFinite(number) && number >= min && number <= max;
}

function integerInRange(value: string, min: number, max: number): boolean {
  const text = value.trim();
  if (!/^\d+$/.test(text)) return false;
  const number = Number(text);
  return number >= min && number <= max;
}

function validLang(value: string): boolean {
  const text = value.trim();
  return text === "" || LANG.test(text);
}

function validCountry(value: string): boolean {
  const text = value.trim();
  return text === "" || /^[A-Za-z]{2}$/.test(text);
}

/** i18n keys for fields the core would refuse. */
export function llmProblems(draft: LlmDraft): string[] {
  const problems: string[] = [];
  if (!validBaseUrl(draft.baseUrl)) problems.push("settings.invalid.baseUrl");
  if (!validModel(draft.model)) problems.push("settings.invalid.model");
  if (!validSecret(draft.apiKey)) problems.push("settings.invalid.secret");
  if (!numberInRange(draft.temperature, 0, 2)) problems.push("settings.invalid.temperature");
  if (!integerInRange(draft.maxTokens, 1, 128_000)) problems.push("settings.invalid.maxTokens");
  if (!numberInRange(draft.timeoutSeconds, 5, 600)) problems.push("settings.invalid.timeout");
  return problems;
}

export function searchProblems(draft: SearchDraft): string[] {
  const problems: string[] = [];
  if (!validSecret(draft.apiKey)) problems.push("settings.invalid.secret");
  // Turning search off must not be blocked by a half-typed region or language
  // the form is no longer showing.
  if (draft.provider === "none") return problems;
  if (!numberInRange(draft.timeoutSeconds, 1, 120)) problems.push("settings.invalid.timeout");
  if (!validCountry(draft.country)) problems.push("settings.invalid.country");
  if (!validLang(draft.searchLang)) problems.push("settings.invalid.lang");
  if (!validLang(draft.uiLang)) problems.push("settings.invalid.uiLang");
  return problems;
}

export function numberDirty(text: string, saved: number): boolean {
  const value = Number(text.trim());
  if (!Number.isFinite(value)) return true;
  return value !== saved;
}

export function integerDirty(text: string, saved: number): boolean {
  if (!/^\d+$/.test(text.trim())) return true;
  return Number(text.trim()) !== saved;
}

export function llmDirty(draft: LlmDraft, saved: LlmSettings): boolean {
  return (
    draft.baseUrl.trim() !== saved.baseUrl ||
    draft.model.trim() !== saved.model ||
    draft.clearApiKey ||
    Boolean(draft.apiKey.trim()) ||
    numberDirty(draft.temperature, saved.temperature) ||
    integerDirty(draft.maxTokens, saved.maxTokens) ||
    numberDirty(draft.timeoutSeconds, saved.timeoutSeconds)
  );
}

export function searchDirty(
  draft: SearchDraft,
  saved: SearchSettings,
  extractMedia: boolean,
): boolean {
  return (
    draft.provider !== saved.provider ||
    draft.clearApiKey ||
    Boolean(draft.apiKey.trim()) ||
    numberDirty(draft.timeoutSeconds, saved.timeoutSeconds) ||
    draft.country.trim().toUpperCase() !== saved.country.trim().toUpperCase() ||
    draft.searchLang.trim() !== saved.searchLang ||
    draft.uiLang.trim() !== saved.uiLang ||
    draft.extractMedia !== extractMedia
  );
}

export function llmCanProbe(draft: LlmDraft, saved: LlmSettings): boolean {
  if (llmProblems(draft).length > 0) return false;
  if (draft.apiKey.trim()) return true;
  if (draft.clearApiKey) return false;
  return saved.apiKeySet;
}

export function searchCanProbe(draft: SearchDraft, saved: SearchSettings): boolean {
  if (draft.provider === "none" || searchProblems(draft).length > 0) return false;
  if (draft.apiKey.trim()) return true;
  if (draft.clearApiKey) return false;
  return saved.apiKeySet;
}

/** Only the fields that differ. A blank key is not sent, so the saved one stays. */
export function llmPatch(draft: LlmDraft, saved: LlmSettings): Record<string, unknown> | null {
  const llm: Record<string, unknown> = {};
  if (draft.baseUrl.trim() !== saved.baseUrl) llm.baseUrl = draft.baseUrl.trim();
  if (draft.model.trim() !== saved.model) llm.model = draft.model.trim();
  if (draft.apiKey.trim()) llm.apiKey = draft.apiKey.trim();
  else if (draft.clearApiKey) llm.clearApiKey = true;
  if (numberDirty(draft.temperature, saved.temperature) && numberInRange(draft.temperature, 0, 2)) {
    llm.temperature = Number(draft.temperature.trim());
  }
  if (integerDirty(draft.maxTokens, saved.maxTokens) && integerInRange(draft.maxTokens, 1, 128_000)) {
    llm.maxTokens = Number(draft.maxTokens.trim());
  }
  if (
    numberDirty(draft.timeoutSeconds, saved.timeoutSeconds) &&
    numberInRange(draft.timeoutSeconds, 5, 600)
  ) {
    llm.timeoutSeconds = Number(draft.timeoutSeconds.trim());
  }
  return Object.keys(llm).length > 0 ? llm : null;
}

export function searchPatch(
  draft: SearchDraft,
  saved: SearchSettings,
  extractMedia: boolean,
): SettingsPatch | null {
  const patch: SettingsPatch = {};
  const search: Record<string, unknown> = {};
  if (draft.provider !== saved.provider) search.provider = draft.provider;
  if (draft.apiKey.trim()) search.apiKey = draft.apiKey.trim();
  else if (draft.clearApiKey) search.clearApiKey = true;
  if (
    numberDirty(draft.timeoutSeconds, saved.timeoutSeconds) &&
    numberInRange(draft.timeoutSeconds, 1, 120)
  ) {
    search.timeoutSeconds = Number(draft.timeoutSeconds.trim());
  }
  if (draft.country.trim().toUpperCase() !== saved.country.trim().toUpperCase()) {
    search.country = draft.country.trim();
  }
  if (draft.searchLang.trim() !== saved.searchLang) search.searchLang = draft.searchLang.trim();
  if (draft.uiLang.trim() !== saved.uiLang) search.uiLang = draft.uiLang.trim();
  if (Object.keys(search).length > 0) patch.search = search;
  if (draft.extractMedia !== extractMedia) patch.extractMedia = draft.extractMedia;
  return patch.search || patch.extractMedia !== undefined ? patch : null;
}
