import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, FieldGroup, SaveRow, SettingsSection } from "@/components/settings/fields";
import { probeModel, saveSettings } from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import {
  integerDirty,
  isLocked,
  llmCanProbe,
  llmDirty,
  llmDraftFrom,
  llmPatch,
  llmProblems,
  numberDirty,
  type InstallSettings,
  type LlmDraft,
} from "@/lib/install-settings";

/**
 * The model connection shared by every project.
 *
 * A blank key is left alone on purpose: the core never sends the saved key back,
 * so the only way to keep it is to not replace it.
 */
export function ModelSection({
  install,
  onSaved,
  onDirty,
}: {
  install: InstallSettings;
  onSaved: (next: InstallSettings) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<LlmDraft>(() => llmDraftFrom(install.llm));
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const savedKey = JSON.stringify(install.llm);
  useEffect(() => {
    // Keyed on the values, not the object: saving search replaces the whole
    // payload, and that must not wipe a key being typed in this form.
    setDraft(llmDraftFrom(install.llm));
    setShowKey(false);
    // savedKey already describes install.llm. Depending on the object would
    // reset this form whenever another section saved and replaced the payload.
  }, [savedKey]);

  const problems = llmProblems(draft);
  const dirty = llmDirty(draft, install.llm);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);

  const locked = (field: string) => isLocked(install.locked, field);
  // A blank form is not an error. Say what is wrong only on a field the person changed.
  const problem = (key: string, changed: boolean) =>
    changed && problems.includes(key) ? t(key) : null;
  const edit = (patch: Partial<LlmDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
    setError(null);
  };

  const save = async () => {
    const patch = llmPatch(draft, install.llm);
    if (!patch) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveSettings({ llm: patch });
      onSaved(next);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError(errorMessage(err, t("settings.saveFailed")));
    } finally {
      setSaving(false);
    }
  };

  const probe = async () => {
    setProbing(true);
    setError(null);
    setMessage(null);
    try {
      await probeModel(llmPatch(draft, install.llm) ?? {});
      setMessage(t("settings.model.testOk"));
    } catch (err) {
      setError(errorMessage(err, t("settings.model.testFailed")));
    } finally {
      setProbing(false);
    }
  };

  const keyLocked = locked("llm.apiKey");

  return (
    <SettingsSection title={t("settings.model.title")} intro={t("settings.model.intro")}>
      <FieldGroup title={t("settings.model.groupConnection")}>
        <Field
          label={t("settings.model.baseUrl")}
          hint={t("settings.model.baseUrlHint")}
          error={problem("settings.invalid.baseUrl", draft.baseUrl.trim() !== install.llm.baseUrl)}
          locked={locked("llm.baseUrl")}
        >
          <Input
            value={draft.baseUrl}
            disabled={locked("llm.baseUrl")}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={
              Boolean(problem("settings.invalid.baseUrl", draft.baseUrl.trim() !== install.llm.baseUrl)) ||
              undefined
            }
            onChange={(event) => edit({ baseUrl: event.target.value })}
          />
        </Field>
        <Field
          label={t("settings.model.apiKey")}
          hint={
            draft.clearApiKey
              ? t("settings.model.apiKeyClearing")
              : install.llm.apiKeySet
                ? t("settings.model.apiKeyKeep")
                : t("settings.model.apiKeyMissing")
          }
          error={problem("settings.invalid.secret", Boolean(draft.apiKey.trim()) || draft.clearApiKey)}
          locked={keyLocked}
        >
          <div className="flex gap-1">
            <Input
              type={showKey ? "text" : "password"}
              value={draft.apiKey}
              disabled={keyLocked || draft.clearApiKey}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ apiKey: event.target.value, clearApiKey: false })}
            />
            <Button type="button" variant="outline" onClick={() => setShowKey((value) => !value)}>
              {showKey ? t("settings.hide") : t("settings.show")}
            </Button>
          </div>
          {install.llm.apiKeySet && !keyLocked ? (
            <button
              type="button"
              className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={() =>
                edit({ clearApiKey: !draft.clearApiKey, apiKey: "" })
              }
            >
              {draft.clearApiKey ? t("settings.keepKey") : t("settings.clearKey")}
            </button>
          ) : null}
        </Field>
        <Field
          label={t("settings.model.model")}
          hint={t("settings.model.modelHint")}
          error={problem("settings.invalid.model", draft.model.trim() !== install.llm.model)}
          locked={locked("llm.model")}
        >
          <Input
            value={draft.model}
            disabled={locked("llm.model")}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={
              Boolean(problem("settings.invalid.model", draft.model.trim() !== install.llm.model)) ||
              undefined
            }
            onChange={(event) => edit({ model: event.target.value })}
          />
        </Field>
      </FieldGroup>

      <FieldGroup title={t("settings.model.groupGeneration")}>
        <Field
          label={t("settings.model.temperature")}
          hint={t("settings.model.temperatureHint")}
          error={problem(
            "settings.invalid.temperature",
            numberDirty(draft.temperature, install.llm.temperature),
          )}
          locked={locked("llm.temperature")}
        >
          <Input
            value={draft.temperature}
            inputMode="decimal"
            disabled={locked("llm.temperature")}
            aria-invalid={
              Boolean(
                problem(
                  "settings.invalid.temperature",
                  numberDirty(draft.temperature, install.llm.temperature),
                ),
              ) || undefined
            }
            onChange={(event) => edit({ temperature: event.target.value })}
          />
          {install.invalid.includes("llm.temperature") ? (
            <span className="text-xs text-muted-foreground">{t("settings.unreadable")}</span>
          ) : null}
        </Field>
        <Field
          label={t("settings.model.maxTokens")}
          hint={t("settings.model.maxTokensHint")}
          error={problem(
            "settings.invalid.maxTokens",
            integerDirty(draft.maxTokens, install.llm.maxTokens),
          )}
          locked={locked("llm.maxTokens")}
        >
          <Input
            value={draft.maxTokens}
            inputMode="numeric"
            disabled={locked("llm.maxTokens")}
            aria-invalid={
              Boolean(
                problem(
                  "settings.invalid.maxTokens",
                  integerDirty(draft.maxTokens, install.llm.maxTokens),
                ),
              ) || undefined
            }
            onChange={(event) => edit({ maxTokens: event.target.value })}
          />
        </Field>
        <Field
          label={t("settings.model.timeout")}
          hint={t("settings.model.timeoutHint")}
          error={problem(
            "settings.invalid.timeout",
            numberDirty(draft.timeoutSeconds, install.llm.timeoutSeconds),
          )}
          locked={locked("llm.timeoutSeconds")}
        >
          <Input
            value={draft.timeoutSeconds}
            inputMode="decimal"
            disabled={locked("llm.timeoutSeconds")}
            aria-invalid={
              Boolean(
                problem(
                  "settings.invalid.timeout",
                  numberDirty(draft.timeoutSeconds, install.llm.timeoutSeconds),
                ),
              ) || undefined
            }
            onChange={(event) => edit({ timeoutSeconds: event.target.value })}
          />
        </Field>
      </FieldGroup>

      <SaveRow
        canSave={dirty && problems.length === 0}
        saving={saving}
        onSave={() => void save()}
        message={message}
        error={error}
      >
        <Button
          type="button"
          variant="outline"
          disabled={!llmCanProbe(draft, install.llm) || probing || saving}
          onClick={() => void probe()}
        >
          {probing ? t("settings.testing") : t("settings.test")}
        </Button>
      </SaveRow>
    </SettingsSection>
  );
}
