/**
 * What choosing a provider fills in before the reader has typed anything.
 *
 * A preset is a starting point, not a restriction: the endpoint and the model
 * stay editable, and a model name can always be typed by hand. The ids are the
 * canonical ones the core stores with a profile.
 */

import type { ProviderId } from "@/lib/install-settings";

export type ProviderPreset = {
  baseUrl: string;
  model: string;
};

export const PROVIDER_PRESETS: Record<ProviderId, ProviderPreset> = {
  openai: {
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
  },
  anthropic: {
    baseUrl: "https://api.anthropic.com/v1",
    model: "claude-sonnet-5-5",
  },
  deepseek: {
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  },
  qwen: {
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
  },
  ollama: {
    baseUrl: "http://127.0.0.1:11434/v1",
    model: "qwen2.5:7b",
  },
  custom: {
    baseUrl: "",
    model: "",
  },
};

/** The picker's order: the table's own, so a new provider is added once. */
export const PROVIDER_IDS = Object.keys(PROVIDER_PRESETS) as ProviderId[];
