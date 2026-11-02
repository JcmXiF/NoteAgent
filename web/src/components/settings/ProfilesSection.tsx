import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, inputClass } from "@/components/ui/input";
import { Field, SaveRow } from "@/components/settings/fields";
import { SaveConflict } from "@/components/settings/SaveConflict";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  activateProfile,
  createProfile,
  deleteProfile,
  listModels,
  updateProfile,
} from "@/lib/api/settings";
import {
  isLocked,
  profileCreateDirty,
  profileCreatePatch,
  profileDraftFrom,
  profileDirty,
  profilePatch,
  profileProblems,
  type InstallSettings,
  type ProfileDraft,
  type ProfileSettings,
  type ProviderId,
} from "@/lib/install-settings";
import { PROVIDER_IDS, PROVIDER_PRESETS } from "@/lib/provider-presets";
import { cn } from "@/lib/utils";

/**
 * The model connections every project shares.
 *
 * There is exactly one active connection. Switching it changes what the next
 * job calls; a job already running keeps the connection it started with. The
 * form never receives a saved key: a blank field means "keep the one in the
 * credential store", and clearing it is a separate, deliberate action.
 */

const BLANK: ProfileDraft = {
  name: "",
  provider: "custom",
  baseUrl: "",
  model: "",
  apiKey: "",
  clearApiKey: false,
};

export function ProfilesSection({
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
  const profiles = install.profiles;
  const firstId = install.activeProfileId ?? profiles[0]?.id ?? null;
  const [selectedId, setSelectedId] = useState<string | null>(firstId);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ProfileDraft>(() => {
    const profile = profiles.find((item) => item.id === firstId);
    return profile ? profileDraftFrom(profile) : { ...BLANK };
  });
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  // A fetched list answers a question about the endpoint it was asked about.
  const fetchToken = useRef(0);

  const selected = profiles.find((item) => item.id === selectedId) ?? null;
  // The environment owns the effective connection. It is shown read-only on
  // the profile every task is using; other stored profiles stay editable,
  // because they do not describe what the process is using right now.
  // A profile being created is not active yet, so the environment's lock on
  // the current connection must not leak into its form.
  const lockedActive = (field: string) =>
    !creating && selected?.active === true && isLocked(install.locked, field);
  const dirty = creating
    ? profileCreateDirty(draft)
    : selected
      ? profileDirty(draft, selected)
      : false;

  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  // Follow the saved document while the reader is not editing, so a save from
  // another section does not leave this form showing values that no longer exist.
  useEffect(() => {
    if (creating) return;
    const profile = profiles.find((item) => item.id === selectedId);
    if (profile && !dirty) setDraft(profileDraftFrom(profile));
  }, [profiles, selectedId, creating, dirty]);

  // A profile deleted elsewhere falls back to the active one.
  useEffect(() => {
    if (creating) return;
    if (selectedId && profiles.some((item) => item.id === selectedId)) return;
    const next = profiles.find((item) => item.id === install.activeProfileId) ?? profiles[0];
    setSelectedId(next?.id ?? null);
    if (!next) setDraft({ ...BLANK });
  }, [profiles, selectedId, creating, install.activeProfileId]);

  const clearSuggestions = () => {
    fetchToken.current += 1;
    setModels([]);
    setModelError(null);
    setFetchingModels(false);
  };

  const edit = (patch: Partial<ProfileDraft>) => {
    if (patch.baseUrl !== undefined || patch.provider !== undefined) {
      clearSuggestions();
    } else {
      setModelError(null);
    }
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
    setError(null);
    setConflict(false);
  };

  const editProvider = (provider: ProviderId) => {
    const preset = PROVIDER_PRESETS[provider];
    const patch: Partial<ProfileDraft> = { provider };
    if (provider !== "custom") {
      // A preset is a starting point: it fills the two fields it knows about,
      // but never a value the environment owns.
      if (!lockedActive("llm.baseUrl")) patch.baseUrl = preset.baseUrl;
      if (!lockedActive("llm.model")) patch.model = preset.model;
    }
    edit(patch);
  };

  const fetchModels = async () => {
    const token = ++fetchToken.current;
    setFetchingModels(true);
    setModelError(null);
    try {
      const result = await listModels({
        baseUrl: draft.baseUrl.trim(),
        provider: draft.provider,
        ...(draft.apiKey.trim() ? { apiKey: draft.apiKey.trim() } : {}),
        ...(!creating && selected ? { profileId: selected.id } : {}),
      });
      if (token !== fetchToken.current) return;
      setModels(result.models);
      if (result.models.length === 0) {
        setModelError(t("settings.profiles.noModels"));
      }
    } catch (err) {
      if (token !== fetchToken.current) return;
      setModels([]);
      setModelError(errorMessage(err, t("settings.profiles.modelsFailed")));
    } finally {
      if (token === fetchToken.current) setFetchingModels(false);
    }
  };

  const select = (profile: ProfileSettings) => {
    setCreating(false);
    setSelectedId(profile.id);
    setDraft(profileDraftFrom(profile));
    setShowKey(false);
    setConfirmingDelete(false);
    clearSuggestions();
    setMessage(null);
    setError(null);
    setConflict(false);
  };

  const startCreate = () => {
    setCreating(true);
    setDraft({ ...BLANK });
    setShowKey(false);
    setConfirmingDelete(false);
    clearSuggestions();
    setMessage(null);
    setError(null);
    setConflict(false);
  };

  const cancelCreate = () => {
    setCreating(false);
    const profile =
      selected ??
      profiles.find((item) => item.id === install.activeProfileId) ??
      profiles[0];
    if (profile) {
      setSelectedId(profile.id);
      setDraft(profileDraftFrom(profile));
    } else {
      setDraft({ ...BLANK });
    }
    clearSuggestions();
    setMessage(null);
    setError(null);
    setConflict(false);
  };

  const showError = (err: unknown) => {
    if (err instanceof ApiError && err.code === "settings.staleRevision") {
      // The draft stays exactly as typed; only the revision behind it is stale.
      setConflict(true);
      return;
    }
    setError(errorMessage(err, t("settings.profiles.saveFailed")));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    setConflict(false);
    setMessage(null);
    try {
      let next: InstallSettings;
      if (creating) {
        const before = new Set(profiles.map((profile) => profile.id));
        next = await createProfile(profileCreatePatch(draft, install.revision));
        const created =
          next.profiles.find((profile) => !before.has(profile.id)) ??
          next.profiles.find((profile) => profile.id === next.activeProfileId);
        setCreating(false);
        setDraft((current) => ({ ...current, apiKey: "", clearApiKey: false }));
        if (created) setSelectedId(created.id);
      } else if (selected) {
        next = await updateProfile(
          selected.id,
          profilePatch(draft, selected, install.revision),
        );
        setDraft((current) => ({ ...current, apiKey: "", clearApiKey: false }));
      } else {
        return;
      }
      onSaved(next);
      setMessage(t("settings.saved"));
    } catch (err) {
      showError(err);
    } finally {
      setSaving(false);
    }
  };

  const makeActive = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    setConflict(false);
    setMessage(null);
    try {
      onSaved(await activateProfile(selected.id, install.revision));
      setMessage(t("settings.saved"));
    } catch (err) {
      showError(err);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    setConflict(false);
    setMessage(null);
    try {
      const next = await deleteProfile(selected.id, install.revision);
      const remaining =
        next.profiles.find((profile) => profile.id === next.activeProfileId) ??
        next.profiles[0] ??
        null;
      setConfirmingDelete(false);
      setCreating(false);
      setSelectedId(remaining?.id ?? null);
      setDraft(remaining ? profileDraftFrom(remaining) : { ...BLANK });
      onSaved(next);
    } catch (err) {
      showError(err);
    } finally {
      setSaving(false);
    }
  };

  const refresh = async () => {
    await onReload();
    setConflict(false);
    setMessage(t("settings.refreshed"));
  };

  const providerLabel = (profile: ProfileSettings) =>
    t(`settings.profiles.provider_${profile.provider}`);
  const modelLabel = (profile: ProfileSettings) =>
    profile.model || t("settings.profiles.noModel");

  // A conflict can be reached from the create form as well as from an edit, so
  // the prompt is one element both paths render. The draft is never touched.
  const conflictNotice = conflict ? <SaveConflict onRefresh={refresh} /> : null;

  const problems = profileProblems(draft);
  const canSave = dirty && problems.length === 0;
  const problem = (key: string) => (dirty && problems.includes(key) ? t(key) : null);

  const keyHint = !install.keychainAvailable
    ? t("settings.profiles.apiKeyUnavailable")
    : draft.clearApiKey
      ? t("settings.profiles.apiKeyClearing")
      : !creating && selected?.apiKeySet
        ? t("settings.profiles.apiKeyKeep")
        : t("settings.profiles.apiKeyMissing");

  const fields = (
    <>
      <Field
        label={t("settings.profiles.name")}
        hint={t("settings.profiles.nameHint")}
        error={problem("settings.invalid.name")}
      >
        <Input
          aria-label={t("settings.profiles.name")}
          value={draft.name}
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => edit({ name: event.target.value })}
        />
      </Field>
      <Field label={t("settings.profiles.provider")}>
        <select
          aria-label={t("settings.profiles.provider")}
          className={cn(inputClass, "h-8 text-sm")}
          value={draft.provider}
          onChange={(event) => editProvider(event.target.value as ProviderId)}
        >
          {PROVIDER_IDS.map((provider) => (
            <option key={provider} value={provider}>
              {t(`settings.profiles.provider_${provider}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label={t("settings.profiles.baseUrl")}
        hint={t("settings.profiles.baseUrlHint")}
        error={problem("settings.invalid.baseUrl")}
        locked={lockedActive("llm.baseUrl")}
      >
        <Input
          aria-label={t("settings.profiles.baseUrl")}
          value={draft.baseUrl}
          disabled={lockedActive("llm.baseUrl")}
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => edit({ baseUrl: event.target.value })}
        />
      </Field>
      <Field
        label={t("settings.profiles.apiKey")}
        hint={keyHint}
        error={problem("settings.invalid.secret")}
        locked={lockedActive("llm.apiKey")}
      >
        <div className="flex gap-1">
          <Input
            aria-label={t("settings.profiles.apiKey")}
            type={showKey ? "text" : "password"}
            value={draft.apiKey}
            disabled={draft.clearApiKey || lockedActive("llm.apiKey")}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => edit({ apiKey: event.target.value, clearApiKey: false })}
          />
          <Button type="button" variant="outline" onClick={() => setShowKey((value) => !value)}>
            {showKey ? t("settings.hide") : t("settings.show")}
          </Button>
        </div>
        {!creating && selected?.apiKeySet && !lockedActive("llm.apiKey") ? (
          <button
            type="button"
            className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => edit({ clearApiKey: !draft.clearApiKey, apiKey: "" })}
          >
            {draft.clearApiKey ? t("settings.keepKey") : t("settings.clearKey")}
          </button>
        ) : null}
      </Field>
      <Field
        label={t("settings.profiles.model")}
        hint={t("settings.profiles.modelHint")}
        error={modelError}
        locked={lockedActive("llm.model")}
      >
        <div className="flex gap-1">
          <Input
            aria-label={t("settings.profiles.model")}
            value={draft.model}
            disabled={lockedActive("llm.model")}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => edit({ model: event.target.value })}
          />
          <Button
            type="button"
            variant="outline"
            disabled={
              problems.includes("settings.invalid.baseUrl") ||
              fetchingModels ||
              saving ||
              lockedActive("llm.model")
            }
            onClick={() => void fetchModels()}
          >
            <RefreshCw className={fetchingModels ? "animate-spin" : undefined} />
            {fetchingModels
              ? t("settings.profiles.fetchingModels")
              : t("settings.profiles.fetchModels")}
          </Button>
        </div>
        {models.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {models.map((model) => (
              <button
                key={model}
                type="button"
                onClick={() => edit({ model })}
                className={cn(
                  "rounded-full border px-2.5 py-1 font-mono text-[11px]",
                  model === draft.model
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border text-muted-foreground hover:bg-muted",
                )}
              >
                {model}
              </button>
            ))}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">
            {t("settings.profiles.modelDiscoveryHint")}
          </span>
        )}
      </Field>
    </>
  );

  const createForm = (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{t("settings.profiles.newTitle")}</h2>
        <Button type="button" variant="ghost" size="sm" onClick={cancelCreate}>
          {t("settings.profiles.cancel")}
        </Button>
      </div>
      {fields}
      {conflictNotice}
      <SaveRow
        canSave={canSave}
        saving={saving}
        onSave={() => void save()}
        message={message}
        error={error}
      />
    </div>
  );

  if (profiles.length === 0) {
    return (
      <section className="flex flex-col gap-5">
        {creating ? (
          <div className="mx-auto w-full max-w-lg">{createForm}</div>
        ) : (
          <div className="flex flex-col items-start gap-3 py-10">
            <h2 className="text-base font-semibold">{t("settings.profiles.emptyTitle")}</h2>
            <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
              {t("settings.profiles.emptyBody")}
            </p>
            <Button type="button" onClick={startCreate}>
              {t("settings.profiles.emptyAction")}
            </Button>
          </div>
        )}
      </section>
    );
  }

  return (
    <div className="flex items-start gap-0">
      <nav
        aria-label={t("settings.profiles.listLabel")}
        className="flex w-56 shrink-0 flex-col gap-1 border-r pr-3"
      >
        <div className="flex items-center justify-between gap-2 pb-2">
          <div>
            <div className="text-sm font-semibold">{t("settings.profiles.title")}</div>
            <div className="text-[11px] text-muted-foreground">
              {t("settings.profiles.count", { count: profiles.length })}
            </div>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={startCreate}>
            <Plus className="size-3.5" />
            {t("settings.profiles.add")}
          </Button>
        </div>
        {profiles.map((profile) => (
          <button
            key={profile.id}
            type="button"
            onClick={() => select(profile)}
            className={cn(
              "flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors",
              profile.id === selectedId && !creating
                ? "border-border bg-accent"
                : "border-transparent hover:bg-accent/50",
            )}
          >
            <span className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{profile.name}</span>
              {profile.active ? (
                <Badge tone="success" className="ml-auto">
                  {t("settings.profiles.active")}
                </Badge>
              ) : null}
            </span>
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Server className="size-3" />
              {providerLabel(profile)}
              <span className="text-border">/</span>
              <span className="truncate font-mono">{modelLabel(profile)}</span>
            </span>
          </button>
        ))}
        <p className="mt-auto pt-4 text-[11px] leading-relaxed text-muted-foreground">
          {t("settings.profiles.intro")}
        </p>
      </nav>

      <div className="min-w-0 flex-1 pl-5">
        {creating ? (
          createForm
        ) : selected ? (
          <div className="flex flex-col gap-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-base font-semibold">{selected.name}</div>
                <div className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                  {providerLabel(selected)}
                  <span className="text-border">/</span>
                  <span className="truncate font-mono">{modelLabel(selected)}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {selected.active ? (
                  <Badge tone="success">
                    <Check className="size-3" />
                    {t("settings.profiles.current")}
                  </Badge>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={saving}
                    onClick={() => void makeActive()}
                  >
                    {t("settings.profiles.makeActive")}
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("settings.profiles.delete")}
                  onClick={() => setConfirmingDelete(true)}
                >
                  <Trash2 />
                </Button>
              </div>
            </div>

            {confirmingDelete ? (
              <div
                role="alert"
                className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3"
              >
                <span className="min-w-0 flex-1 text-xs leading-relaxed">
                  {t("settings.profiles.deleteTitle")}{" "}
                  {t("settings.profiles.deleteBody", { name: selected.name })}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmingDelete(false)}
                >
                  {t("settings.profiles.cancel")}
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  disabled={saving}
                  onClick={() => void remove()}
                >
                  {t("settings.profiles.deleteConfirm")}
                </Button>
              </div>
            ) : null}

            {fields}

            {conflictNotice}

            <SaveRow
              canSave={canSave}
              saving={saving}
              onSave={() => void save()}
              message={message}
              error={error}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
