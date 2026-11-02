/**
 * Install settings and the project brief.
 *
 * Both are stored by the core. The key never comes back: a later save that
 * leaves the field blank keeps the one already stored.
 */

import { deleteJson, getJson, postJson, putJson } from "@/lib/api/client";
import type {
  InstallSettings,
  ProfileCreatePatch,
  ProfileUpdatePatch,
  SettingsPatch,
} from "@/lib/install-settings";

export type ProjectBrief = {
  text: string;
  source: "project" | "legacy" | "default";
  defaultText: string;
  maxChars: number;
};

export function fetchSettings(): Promise<InstallSettings> {
  return getJson<InstallSettings>("/settings");
}

export function saveSettings(patch: SettingsPatch): Promise<InstallSettings> {
  return putJson<InstallSettings>("/settings", patch);
}

/** A new connection; the payload leaves a blank key out rather than sending it. */
export function createProfile(
  patch: ProfileCreatePatch,
): Promise<InstallSettings> {
  return postJson<InstallSettings>("/settings/profiles", patch);
}

/** The fields a draft actually changed; the revision makes a stale save a 409. */
export function updateProfile(
  id: string,
  patch: ProfileUpdatePatch,
): Promise<InstallSettings> {
  return putJson<InstallSettings>(
    `/settings/profiles/${encodeURIComponent(id)}`,
    patch,
  );
}

export function deleteProfile(id: string, revision: number): Promise<InstallSettings> {
  return deleteJson<InstallSettings>(
    `/settings/profiles/${encodeURIComponent(id)}?revision=${revision}`,
  );
}

export function activateProfile(id: string, revision: number): Promise<InstallSettings> {
  return postJson<InstallSettings>(
    `/settings/profiles/${encodeURIComponent(id)}/activate`,
    { revision },
  );
}

export function probeModel(patch: Record<string, unknown>): Promise<{ ok: true }> {
  return postJson<{ ok: true }>("/settings/llm/probe", patch);
}

export function probeSearch(
  patch: Record<string, unknown>,
): Promise<{ ok: true; count: number }> {
  return postJson<{ ok: true; count: number }>("/settings/search/probe", patch);
}

export function fetchBrief(project: string): Promise<ProjectBrief> {
  return getJson<ProjectBrief>(`/projects/${encodeURIComponent(project)}/brief`);
}

export function saveBrief(project: string, text: string): Promise<ProjectBrief> {
  return putJson<ProjectBrief>(`/projects/${encodeURIComponent(project)}/brief`, { text });
}
