import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/settings/fields";
import { errorMessage, sentenceForCode } from "@/lib/api/client";
import { runDeepCheck } from "@/lib/api/settings";
import {
  deepCheckLabelKey,
  type DeepCheckItem,
  type DeepCheckResult,
  type InstallSettings,
} from "@/lib/install-settings";

/** A failed check's sentence: its code first, then the screen's own fallback. */
function failureText(item: DeepCheckItem, fallback: string): string {
  return (item.code ? sentenceForCode(item.code) : "") || fallback;
}

/**
 * The explicit check the save-time test cannot make: can this model call tools,
 * and does the native search its connection offers actually answer?
 *
 * It reads the saved configuration and writes nothing. The button is the
 * reader's choice because both checks are real provider requests.
 */
export function DeepCheckSection({ install }: { install: InstallSettings }) {
  const { t } = useTranslation();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DeepCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A save changes what the check would test, so an answer that arrives
  // afterwards belongs to a configuration that is no longer in effect.
  const checkToken = useRef(0);

  useEffect(() => {
    checkToken.current += 1;
    setResult(null);
    setError(null);
    setRunning(false);
  }, [install.revision]);

  const run = async () => {
    const token = ++checkToken.current;
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      const answer = await runDeepCheck();
      if (token !== checkToken.current) return;
      for (const item of answer.checks) {
        if (!item.ok && item.detail) {
          console.info(`[noteagent] deep check ${item.name}: ${item.detail}`);
        }
      }
      setResult(answer);
    } catch (err) {
      if (token !== checkToken.current) return;
      setError(errorMessage(err, t("settings.deepCheck.requestFailed")));
    } finally {
      if (token === checkToken.current) setRunning(false);
    }
  };

  return (
    <SettingsSection
      title={t("settings.deepCheck.title")}
      intro={t("settings.deepCheck.intro")}
    >
      <div className="flex flex-col gap-2">
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("settings.deepCheck.costWarning")}
        </p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("settings.deepCheck.savedOnly")}
        </p>
        {!install.llm.configured ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("settings.deepCheck.notConfigured")}
          </p>
        ) : null}

        <div>
          <Button
            type="button"
            variant="outline"
            disabled={!install.llm.configured || running}
            onClick={() => void run()}
          >
            {running
              ? t("settings.deepCheck.running")
              : t("settings.deepCheck.run")}
          </Button>
        </div>

        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {result ? (
          <div className="flex flex-col gap-1">
            <p
              role="status"
              className={
                result.ok
                  ? "text-xs text-success"
                  : "text-xs text-destructive"
              }
            >
              {result.ok
                ? t("settings.deepCheck.passed")
                : t("settings.deepCheck.hasFailures")}
            </p>
            <ul className="flex flex-col gap-1">
              {result.checks.map((item) => (
                <li key={item.name} className="flex flex-col gap-0.5 text-xs">
                  <span className="font-medium">
                    {t(deepCheckLabelKey(item.name))}
                  </span>
                  <span
                    className={
                      item.ok ? "text-success" : "text-destructive"
                    }
                  >
                    {item.ok
                      ? t("settings.deepCheck.verified")
                      : failureText(
                          item,
                          t("settings.deepCheck.checkFailed"),
                        )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </SettingsSection>
  );
}
