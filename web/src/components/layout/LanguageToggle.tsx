import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { IconTip } from "@/components/ui/tooltip";
import {
  LANGUAGE_LABEL,
  SUPPORTED_LANGUAGES,
  currentLanguage,
  setLanguage,
  type Language,
} from "@/i18n";

/** Cycles through the interface languages. */
export function LanguageToggle() {
  const { t } = useTranslation();
  const active = currentLanguage();
  const next: Language =
    SUPPORTED_LANGUAGES[(SUPPORTED_LANGUAGES.indexOf(active) + 1) % SUPPORTED_LANGUAGES.length];

  return (
    <IconTip
      label={t("language.switchTo", {
        current: LANGUAGE_LABEL[active],
        next: LANGUAGE_LABEL[next],
      })}
    >
      <button
        type="button"
        onClick={() => setLanguage(next)}
        aria-label={t("language.switch")}
        className="flex size-9 flex-col items-center justify-center gap-0 rounded-md text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
      >
        <Languages className="size-[18px]" />
      </button>
    </IconTip>
  );
}
