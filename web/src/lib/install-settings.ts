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

/** Where a resolved value came from, as the core reports it. */
export type ValueSource = "env" | "config" | "keychain" | "default";

/** A source the core named, or the default when it named none this build knows. */
export function valueSource(
  sources: Record<string, string>,
  field: string,
): ValueSource {
  const source = sources[field];
  return source === "env" || source === "config" || source === "keychain"
    ? source
    : "default";
}

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
  /** Where each value came from, keyed by the field id the core reports. */
  sources: Record<string, string>;
  configPath: string;
  schemaVersion: number;
};

/** The generation values shared by every connection. */
export type Generation = {
  temperature: number;
  maxTokens: number;
  timeoutSeconds: number;
};

export type GenerationDraft = {
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

export function generationDraftFrom(generation: Generation): GenerationDraft {
  return {
    temperature: String(generation.temperature),
    maxTokens: String(generation.maxTokens),
    timeoutSeconds: String(generation.timeoutSeconds),
  };
}

export function generationDirty(draft: GenerationDraft, saved: Generation): boolean {
  return (
    numberDirty(draft.temperature, saved.temperature) ||
    integerDirty(draft.maxTokens, saved.maxTokens) ||
    numberDirty(draft.timeoutSeconds, saved.timeoutSeconds)
  );
}

const GENERATION_FIELDS = [
  {
    key: "temperature",
    min: 0,
    max: 2,
    integer: false,
    problem: "settings.invalid.temperature",
  },
  {
    key: "maxTokens",
    min: 1,
    max: 128_000,
    integer: true,
    problem: "settings.invalid.maxTokens",
  },
  {
    key: "timeoutSeconds",
    min: 5,
    max: 600,
    integer: false,
    problem: "settings.invalid.timeout",
  },
] as const;

type GenerationField = (typeof GENERATION_FIELDS)[number];

function generationInRange(text: string, field: GenerationField): boolean {
  return field.integer
    ? integerInRange(text, field.min, field.max)
    : numberInRange(text, field.min, field.max);
}

function generationChanged(
  text: string,
  saved: number,
  field: GenerationField,
): boolean {
  return field.integer ? integerDirty(text, saved) : numberDirty(text, saved);
}

/** i18n keys for values the core would refuse on a generation save. */
export function generationProblems(draft: GenerationDraft): string[] {
  return GENERATION_FIELDS.filter(
    (field) => !generationInRange(draft[field.key], field),
  ).map((field) => field.problem);
}

/** Only the parameters that differ and are valid. */
export function generationPatch(
  draft: GenerationDraft,
  saved: Generation,
): Record<string, number> | null {
  const patch: Record<string, number> = {};
  for (const field of GENERATION_FIELDS) {
    if (
      generationChanged(draft[field.key], saved[field.key], field) &&
      generationInRange(draft[field.key], field)
    ) {
      patch[field.key] = Number(draft[field.key].trim());
    }
  }
  return Object.keys(patch).length > 0 ? patch : null;
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

export function searchCanProbe(draft: SearchDraft, saved: SearchSettings): boolean {
  if (draft.provider === "none" || searchProblems(draft).length > 0) return false;
  if (draft.apiKey.trim()) return true;
  if (draft.clearApiKey) return false;
  return saved.apiKeySet;
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
