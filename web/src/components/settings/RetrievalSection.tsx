import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  SaveRow,
  SettingsSection,
  SourceBadge,
} from "@/components/settings/fields";
import { saveSettings } from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import {
  isLocked,
  ragDirty,
  ragDraftFrom,
  ragPatch,
  ragProblems,
  type InstallSettings,
  type RagDraft,
} from "@/lib/install-settings";

/**
 * Hybrid retrieval for this computer.
 *
 * One switch, one embedding service. The service fields stay out of the way
 * until the switch is on, and the core refuses a switch that names no service
 * to call. Saving changes the next run, not the one already answering.
 */
export function RetrievalSection({
  install,
  onSaved,
  onDirty,
}: {
  install: InstallSettings;
  onSaved: (next: InstallSettings) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<RagDraft>(() => ragDraftFrom(install.rag));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const savedKey = JSON.stringify(install.rag);
  useEffect(() => {
    setDraft(ragDraftFrom(install.rag));
  }, [savedKey]);

  const problems = ragProblems(draft);
  const dirty = ragDirty(draft, install.rag);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);

  const locked = (field: string) => isLocked(install.locked, field);
  const problem = (key: string) => (problems.includes(key) ? t(key) : null);
  const edit = (patch: Partial<RagDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
    setError(null);
  };

  const save = async () => {
    const patch = ragPatch(draft, install.rag);
    if (!patch) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveSettings({ ...patch, revision: install.revision });
      onSaved(next);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError(errorMessage(err, t("settings.saveFailed")));
    } finally {
      setSaving(false);
    }
  };

  const toggleLocked = locked("rag.enabled");

  return (
    <SettingsSection
      title={t("settings.retrieval.title")}
      intro={t("settings.retrieval.intro")}
    >
      <div className="flex items-start gap-2">
        <Checkbox
          className="mt-0.5"
          checked={draft.enabled}
          disabled={toggleLocked}
          aria-label={t("settings.retrieval.enabled")}
          onCheckedChange={(checked) => edit({ enabled: checked === true })}
        />
        <span className="flex flex-col gap-0.5">
          <span className="flex items-center gap-2">
            <span className="text-sm">{t("settings.retrieval.enabled")}</span>
            {toggleLocked ? <SourceBadge source="env" /> : null}
          </span>
          <span className="text-xs leading-relaxed text-muted-foreground">
            {toggleLocked
              ? t("settings.locked")
              : t("settings.retrieval.enabledHint")}
          </span>
        </span>
      </div>

      {draft.enabled ? (
        <FieldGroup title={t("settings.retrieval.groupService")}>
          <Field
            label={t("settings.retrieval.endpoint")}
            hint={t("settings.retrieval.endpointHint")}
            error={problem("settings.invalid.ragEndpoint")}
            locked={locked("rag.embedding.endpoint")}
          >
            <Input
              aria-label={t("settings.retrieval.endpoint")}
              value={draft.endpoint}
              disabled={locked("rag.embedding.endpoint")}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ endpoint: event.target.value })}
            />
          </Field>
          <Field
            label={t("settings.retrieval.model")}
            hint={t("settings.retrieval.modelHint")}
            error={problem("settings.invalid.ragModel")}
            locked={locked("rag.embedding.model")}
          >
            <Input
              aria-label={t("settings.retrieval.model")}
              value={draft.model}
              disabled={locked("rag.embedding.model")}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ model: event.target.value })}
            />
          </Field>
        </FieldGroup>
      ) : null}

      {draft.enabled ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("settings.retrieval.outbound")}
        </p>
      ) : null}

      <SaveRow
        canSave={dirty && problems.length === 0}
        saving={saving}
        onSave={() => void save()}
        message={message}
        error={error}
      >
        {draft.enabled ? (
          <span className="text-xs text-muted-foreground">
            {t("settings.retrieval.takesEffect")}
          </span>
        ) : null}
      </SaveRow>
    </SettingsSection>
  );
}
