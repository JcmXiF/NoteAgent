import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, inputClass } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  SaveRow,
  SettingsSection,
  SourceBadge,
} from "@/components/settings/fields";
import {
  fetchRagStatus,
  rebuildRagIndex,
  saveSettings,
  type RagIndexState,
  type RagIndexStatus,
  type RagRebuildReport,
} from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import i18n from "@/i18n";
import {
  isLocked,
  ragDirty,
  ragDraftFrom,
  ragPatch,
  ragProblems,
  type InstallSettings,
  type RagDraft,
} from "@/lib/install-settings";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

const STATE_LABELS: Record<RagIndexState, string> = {
  disabled: "settings.retrieval.statusDisabled",
  unavailable: "settings.retrieval.statusUnavailable",
  cold: "settings.retrieval.statusCold",
  stale: "settings.retrieval.statusStale",
  ready: "settings.retrieval.statusReady",
};

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
  const currentProject = useWorkspaceStore((state) => state.current);
  const [draft, setDraft] = useState<RagDraft>(() => ragDraftFrom(install.rag));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [status, setStatus] = useState<RagIndexStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [report, setReport] = useState<RagRebuildReport | null>(null);
  const [rebuildError, setRebuildError] = useState<string | null>(null);

  const savedKey = JSON.stringify(install.rag);
  useEffect(() => {
    setDraft(ragDraftFrom(install.rag));
    setShowKey(false);
  }, [savedKey]);

  const refreshStatus = useCallback(async () => {
    setLoadingStatus(true);
    setStatusError(null);
    // A new question, not an answer: the previous project's state must not sit
    // beside a failure or a spinner.
    setStatus(null);
    try {
      setStatus(await fetchRagStatus());
    } catch (err) {
      setStatusError(
        errorMessage(err, i18n.t("settings.retrieval.statusFailedToLoad")),
      );
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  // The answer describes the active project and the saved switch, so a save or
  // a project change is a reason to ask again.
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus, savedKey, currentProject]);

  const problems = ragProblems(draft, install.locked);
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

  const rebuild = async () => {
    setRebuilding(true);
    setRebuildError(null);
    setReport(null);
    try {
      const result = await rebuildRagIndex();
      setReport(result);
      await refreshStatus();
    } catch (err) {
      setRebuildError(errorMessage(err, t("settings.retrieval.rebuildFailed")));
    } finally {
      setRebuilding(false);
    }
  };

  const toggleLocked = locked("rag.enabled");
  const keyLocked = locked("rag.embedding.apiKey");
  const stateKey = status
    ? (STATE_LABELS[status.state] ?? "settings.retrieval.statusUnavailable")
    : "";
  const indexed =
    status !== null &&
    status.state !== "disabled" &&
    status.state !== "unavailable"
      ? status
      : null;
  const canRebuild = indexed !== null;

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
        <>
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

          <Button
            type="button"
            variant="ghost"
            className="justify-start px-0"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            {t("settings.retrieval.advanced")}
          </Button>

          {advancedOpen ? (
            <FieldGroup title={t("settings.retrieval.groupAdvanced")}>
              <Field
                label={t("settings.retrieval.queryPrefix")}
                hint={t("settings.retrieval.queryPrefixHint")}
                error={problem("settings.invalid.ragQueryPrefix")}
                locked={locked("rag.embedding.queryPrefix")}
              >
                <textarea
                  aria-label={t("settings.retrieval.queryPrefix")}
                  className={cn(
                    inputClass,
                    "h-auto min-h-24 py-2 font-mono text-xs leading-relaxed",
                  )}
                  value={draft.queryPrefix}
                  disabled={locked("rag.embedding.queryPrefix")}
                  spellCheck={false}
                  onChange={(event) =>
                    edit({ queryPrefix: event.target.value })
                  }
                />
              </Field>
              <Field
                label={t("settings.retrieval.batch")}
                hint={t("settings.retrieval.batchHint")}
                error={problem("settings.invalid.ragBatch")}
                locked={locked("rag.embedding.batch")}
              >
                <Input
                  aria-label={t("settings.retrieval.batch")}
                  value={draft.batch}
                  inputMode="numeric"
                  disabled={locked("rag.embedding.batch")}
                  onChange={(event) => edit({ batch: event.target.value })}
                />
              </Field>
              <Field
                label={t("settings.retrieval.concurrency")}
                hint={t("settings.retrieval.concurrencyHint")}
                error={problem("settings.invalid.ragConcurrency")}
                locked={locked("rag.embedding.concurrency")}
              >
                <Input
                  aria-label={t("settings.retrieval.concurrency")}
                  value={draft.concurrency}
                  inputMode="numeric"
                  disabled={locked("rag.embedding.concurrency")}
                  onChange={(event) =>
                    edit({ concurrency: event.target.value })
                  }
                />
              </Field>
              <Field
                label={t("settings.retrieval.apiKey")}
                hint={
                  draft.clearApiKey
                    ? t("settings.retrieval.apiKeyClearing")
                    : install.rag.embedding.apiKeySet
                      ? t("settings.retrieval.apiKeyKeep")
                      : t("settings.retrieval.apiKeyMissing")
                }
                error={problem("settings.invalid.secret")}
                locked={keyLocked}
              >
                <div className="flex gap-1">
                  <Input
                    type={showKey ? "text" : "password"}
                    aria-label={t("settings.retrieval.apiKey")}
                    value={draft.apiKey}
                    disabled={keyLocked || draft.clearApiKey}
                    spellCheck={false}
                    autoComplete="off"
                    onChange={(event) =>
                      edit({
                        apiKey: event.target.value,
                        clearApiKey: false,
                      })
                    }
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowKey((value) => !value)}
                  >
                    {showKey ? t("settings.hide") : t("settings.show")}
                  </Button>
                </div>
                {install.rag.embedding.apiKeySet && !keyLocked ? (
                  <button
                    type="button"
                    className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
                    onClick={() =>
                      edit({
                        clearApiKey: !draft.clearApiKey,
                        apiKey: "",
                      })
                    }
                  >
                    {draft.clearApiKey
                      ? t("settings.keepKey")
                      : t("settings.clearKey")}
                  </button>
                ) : null}
              </Field>
            </FieldGroup>
          ) : null}

          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("settings.retrieval.outbound")}
          </p>
        </>
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

      <FieldGroup title={t("settings.retrieval.statusTitle")}>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium">
            {status
              ? t(stateKey)
              : statusError
                ? t("settings.retrieval.statusUnavailable")
                : t("settings.retrieval.statusLoading")}
          </span>
          {indexed ? (
            <>
              <span className="text-xs text-muted-foreground">
                {t("settings.retrieval.statusCounts", {
                  units: indexed.units,
                  chunks: indexed.chunks,
                })}
              </span>
              {indexed.failed > 0 ? (
                <span className="text-xs text-destructive">
                  {t("settings.retrieval.statusFailed", {
                    count: indexed.failed,
                  })}
                </span>
              ) : null}
            </>
          ) : null}
          <div className="ml-auto flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={loadingStatus || rebuilding}
              onClick={() => void refreshStatus()}
            >
              {loadingStatus
                ? t("settings.retrieval.refreshing")
                : t("settings.retrieval.refresh")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!canRebuild || rebuilding || loadingStatus}
              onClick={() => void rebuild()}
            >
              {rebuilding
                ? t("settings.retrieval.rebuilding")
                : t("settings.retrieval.rebuild")}
            </Button>
          </div>
        </div>
        {status?.reason && status.state !== "disabled" ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {status.reason}
          </p>
        ) : null}
        {statusError ? (
          <p role="alert" className="text-xs text-destructive">
            {statusError}
          </p>
        ) : null}
        {rebuildError ? (
          <p role="alert" className="text-xs text-destructive">
            {rebuildError}
          </p>
        ) : null}
        {report ? (
          <div className="flex flex-col gap-1">
            <p className="text-xs text-muted-foreground">
              {t("settings.retrieval.rebuildDone", {
                added: report.added,
                updated: report.updated,
                unchanged: report.unchanged,
                deleted: report.deleted,
                embedded: report.embeddedChunks,
              })}
            </p>
            {report.failures.length > 0 ? (
              <div className="flex flex-col gap-0.5">
                <span className="text-xs font-medium">
                  {t("settings.retrieval.rebuildFailures")}
                </span>
                <ul className="flex flex-col gap-0.5">
                  {report.failures.map((failure) => (
                    <li
                      key={failure.unitId}
                      className="text-xs text-destructive"
                    >
                      {failure.pageId}: {failure.reason}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </FieldGroup>
    </SettingsSection>
  );
}
