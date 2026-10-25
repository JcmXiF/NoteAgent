/**
 * Install settings and the project brief.
 *
 * Both are stored by the core. The key never comes back: a later save that
 * leaves the field blank keeps the one already stored.
 */

import { getJson, postJson, putJson } from "@/lib/api/client";
import type { InstallSettings, SettingsPatch } from "@/lib/install-settings";

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
