/**
 * The settings payload the component tests draw.
 *
 * One shape, so adding a field to the answer is one edit rather than one per
 * test file. Tests override only what they are about.
 */

import type { InstallSettings, ProfileSettings } from "@/lib/install-settings";

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
export function typeInto(element: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}
