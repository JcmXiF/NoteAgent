import { describe, expect, it } from "vitest";
import { PROVIDER_IDS, PROVIDER_PRESETS } from "@/lib/provider-presets";

describe("provider presets", () => {
  it("gives every provider an endpoint and a suggested model", () => {
    for (const provider of PROVIDER_IDS) {
      const preset = PROVIDER_PRESETS[provider];
      expect(preset).toBeDefined();
      if (provider === "custom") continue;
      expect(preset.baseUrl).toMatch(/^https?:\/\//);
      expect(preset.model.length).toBeGreaterThan(0);
    }
  });

  it("leaves a custom endpoint empty so both fields stay the reader's", () => {
    expect(PROVIDER_PRESETS.custom).toEqual({ baseUrl: "", model: "" });
  });

  it("names the endpoints the providers publish", () => {
    expect(PROVIDER_PRESETS.deepseek).toEqual({
      baseUrl: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
    });
    expect(PROVIDER_PRESETS.ollama.baseUrl).toBe("http://127.0.0.1:11434/v1");
  });
});
