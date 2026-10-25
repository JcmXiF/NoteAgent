import { Minus, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Field, SettingsSection } from "@/components/settings/fields";
import {
  LANGUAGE_LABEL,
  SUPPORTED_LANGUAGES,
  currentLanguage,
  setLanguage,
} from "@/i18n";
import { useUiStore } from "@/stores/ui-store";

const THEMES = ["light", "dark", "system"] as const;

/**
 * Choices that belong to this window.
 *
 * They take effect as they are clicked: there is nothing to save, and nothing
 * here is shared with another project.
 */
export function AppearanceSection() {
  const { t } = useTranslation();
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const fontSize = useUiStore((state) => state.fontSize);
  const setFontSize = useUiStore((state) => state.setFontSize);
  const language = currentLanguage();

  return (
    <SettingsSection title={t("settings.appearance.title")} intro={t("settings.appearance.intro")}>
      <Field label={t("settings.appearance.theme")}>
        <div className="flex flex-wrap gap-1">
          {THEMES.map((item) => (
            <Button
              key={item}
              type="button"
              variant={theme === item ? "default" : "outline"}
              onClick={() => setTheme(item)}
            >
              {t(`theme.${item}`)}
            </Button>
          ))}
        </div>
      </Field>
      <Field label={t("settings.appearance.language")}>
        <div className="flex flex-wrap gap-1">
          {SUPPORTED_LANGUAGES.map((item) => (
            <Button
              key={item}
              type="button"
              variant={language === item ? "default" : "outline"}
              onClick={() => setLanguage(item)}
            >
              {LANGUAGE_LABEL[item]}
            </Button>
          ))}
        </div>
      </Field>
      <Field label={t("settings.appearance.fontSize")} hint={t("settings.appearance.fontSizeHint")}>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t("settings.appearance.smaller")}
            disabled={fontSize <= 12}
            onClick={() => setFontSize(fontSize - 1)}
          >
            <Minus />
          </Button>
          <span className="w-8 text-center text-sm tabular-nums">{fontSize}</span>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t("settings.appearance.larger")}
            disabled={fontSize >= 20}
            onClick={() => setFontSize(fontSize + 1)}
          >
            <Plus />
          </Button>
        </div>
      </Field>
    </SettingsSection>
  );
}
