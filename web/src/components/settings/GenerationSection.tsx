import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  SaveRow,
  SettingsSection,
} from "@/components/settings/fields";
import { SaveConflict } from "@/components/settings/SaveConflict";
import { ApiError, errorMessage } from "@/lib/api/client";
import { probeModel, saveSettings } from "@/lib/api/settings";
import {
  generationDirty,
  generationDraftFrom,
  generationPatch,
  generationProblems,
  isLocked,
  type GenerationDraft,
  type InstallSettings,
} from "@/lib/install-settings";

/**
 * How this installation writes, whichever connection is active.
 *
 * The values are global on purpose: a temperature describes the house style,
 * not the endpoint. A value the process environment owns cannot be edited here;
 * it is shown read-only with the source it came from, because the document
 * cannot beat it. Saving runs the same one-request connection test as the
 * button, so a parameter change is tried immediately rather than at the next
 * job; changing a value again forgets that result until it is tested again.
 */
export function GenerationSection({
  install,
  onSaved,
  onDirty,
  onReload,
}: {
  install: InstallSettings;
  onSaved: (next: InstallSettings) => void;
  onDirty: (dirty: boolean) => void;
  /** Re-reads the document after a conflict, without touching the draft. */
  onReload: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<GenerationDraft>(() =>
    generationDraftFrom(install.llm),
  );
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [tested, setTested] = useState<"ok" | "fail" | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // A probe answers a question about the values it was given. An edit while it
  // runs, or a newer probe, makes that answer irrelevant; the counters let the
  // late reply drop itself instead of repainting a stale result.
  const editVersion = useRef(0);
  const probeToken = useRef(0);

  const problems = generationProblems(draft);
  const dirty = generationDirty(draft, install.llm);

  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  useEffect(() => {
    if (!dirty) setDraft(generationDraftFrom(install.llm));
  }, [install.llm, dirty]);

  const locked = (field: string) => isLocked(install.locked, field);

  const edit = (patch: Partial<GenerationDraft>) => {
    editVersion.current += 1;
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
    setError(null);
    setConflict(false);
    // The last answer was about the previous numbers.
    setTested(null);
    setTestError(null);
  };

  const showError = (err: unknown) => {
    if (err instanceof ApiError && err.code === "settings.staleRevision") {
      setConflict(true);
      return;
    }
    setError(errorMessage(err, t("settings.saveFailed")));
  };

  const runProbe = async (patch: Record<string, unknown>) => {
    const version = editVersion.current;
    const token = ++probeToken.current;
    setTesting(true);
    setTested(null);
    setTestError(null);
    try {
      await probeModel(patch);
      if (token !== probeToken.current || version !== editVersion.current) return;
      setTested("ok");
    } catch (err) {
      if (token !== probeToken.current || version !== editVersion.current) return;
      setTested("fail");
      setTestError(errorMessage(err, t("settings.model.testFailed")));
    } finally {
      if (token === probeToken.current) setTesting(false);
    }
  };

  const save = async () => {
    const patch = generationPatch(draft, install.llm);
    if (!patch) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    setConflict(false);
    setTested(null);
    setTestError(null);
    let next: InstallSettings | null = null;
    try {
      next = await saveSettings({ llm: patch, revision: install.revision });
      onSaved(next);
      setMessage(t("settings.saved"));
    } catch (err) {
      showError(err);
    } finally {
      setSaving(false);
    }
    if (next?.llm.configured) await runProbe({});
  };

  const trial = async () => {
    setMessage(null);
    setError(null);
    setConflict(false);
    await runProbe(generationPatch(draft, install.llm) ?? {});
  };

  const refresh = async () => {
    await onReload();
    setConflict(false);
    setMessage(t("settings.refreshed"));
  };

  const problem = (key: string) => (dirty && problems.includes(key) ? t(key) : null);

  return (
    <SettingsSection
      title={t("settings.generation.title")}
      intro={t("settings.generation.intro")}
    >
      <FieldGroup title={t("settings.model.groupGeneration")}>
        <Field
          label={t("settings.model.temperature")}
          hint={t("settings.model.temperatureHint")}
          error={problem("settings.invalid.temperature")}
          locked={locked("llm.temperature")}
        >
          <Input
            aria-label={t("settings.model.temperature")}
            value={draft.temperature}
            inputMode="decimal"
            disabled={locked("llm.temperature")}
            onChange={(event) => edit({ temperature: event.target.value })}
          />
        </Field>
        <Field
          label={t("settings.model.maxTokens")}
          hint={t("settings.model.maxTokensHint")}
          error={problem("settings.invalid.maxTokens")}
          locked={locked("llm.maxTokens")}
        >
          <Input
            aria-label={t("settings.model.maxTokens")}
            value={draft.maxTokens}
            inputMode="numeric"
            disabled={locked("llm.maxTokens")}
            onChange={(event) => edit({ maxTokens: event.target.value })}
          />
        </Field>
        <Field
          label={t("settings.model.timeout")}
          hint={t("settings.model.timeoutHint")}
          error={problem("settings.invalid.timeout")}
          locked={locked("llm.timeoutSeconds")}
        >
          <Input
            aria-label={t("settings.model.timeout")}
            value={draft.timeoutSeconds}
            inputMode="decimal"
            disabled={locked("llm.timeoutSeconds")}
            onChange={(event) => edit({ timeoutSeconds: event.target.value })}
          />
        </Field>
      </FieldGroup>

      {tested === "ok" ? (
        <p role="status" className="text-xs text-success">
          {t("settings.model.testOk")}
        </p>
      ) : null}
      {tested === "fail" ? (
        <p role="alert" className="text-xs text-destructive">
          {testError}
        </p>
      ) : null}
      {!install.llm.configured ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("settings.generation.notConfigured")}
        </p>
      ) : null}

      {conflict ? <SaveConflict onRefresh={refresh} /> : null}

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
          disabled={
            !install.llm.configured || testing || saving || problems.length > 0
          }
          onClick={() => void trial()}
        >
          {testing ? t("settings.testing") : t("settings.test")}
        </Button>
      </SaveRow>
    </SettingsSection>
  );
}
