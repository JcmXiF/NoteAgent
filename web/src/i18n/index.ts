import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zh from "./locales/zh.json";

/**
 * Supported interface languages. Adding one means adding a catalog file and
 * extending this list — `npm run check:i18n` fails when the key sets diverge.
 */
export const SUPPORTED_LANGUAGES = ["en", "zh"] as const;

export type Language = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_LABEL: Record<Language, string> = {
  en: "English",
  zh: "中文",
};

const STORAGE_KEY = "noteagent.language";

function isLanguage(value: unknown): value is Language {
  return (
    typeof value === "string" &&
    (SUPPORTED_LANGUAGES as readonly string[]).includes(value)
  );
}

function detectLanguage(): Language {
  if (typeof window === "undefined") return "en";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (isLanguage(saved)) return saved;
  } catch {
    // Storage can be unavailable; fall through to the navigator hint.
  }
  const preferred = window.navigator?.languages?.[0] ?? window.navigator?.language;
  return preferred?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: detectLanguage(),
  fallbackLng: "en",
  interpolation: {
    // React already escapes interpolated values.
    escapeValue: false,
  },
});

export function currentLanguage(): Language {
  const language = i18n.resolvedLanguage ?? i18n.language;
  return isLanguage(language) ? language : "en";
}

export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Preference stays in memory when storage is unavailable.
  }
  if (typeof document !== "undefined") {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }
}

if (typeof document !== "undefined") {
  document.documentElement.lang = currentLanguage() === "zh" ? "zh-CN" : "en";
}

export default i18n;
