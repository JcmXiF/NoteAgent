import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, inputClass } from "@/components/ui/input";
import { Field, FieldGroup, SaveRow, SettingsSection } from "@/components/settings/fields";
import { probeSearch, saveSettings } from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import {
  isLocked,
  numberDirty,
  searchCanProbe,
  searchDirty,
  searchDraftFrom,
  searchPatch,
  searchProblems,
  type InstallSettings,
  type SearchDraft,
  type SearchProvider,
} from "@/lib/install-settings";
import { cn } from "@/lib/utils";

const PROVIDERS: SearchProvider[] = ["none", "brave", "serper"];

/**
 * Web search for this computer, and whether images are kept when a source is saved.
 *
 * The image choice sits here because it is also an install setting: one answer
 * for every project, stored beside the search key.
 */
export function SearchSection({
  install,
  onSaved,
  onDirty,
}: {
  install: InstallSettings;
  onSaved: (next: InstallSettings) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<SearchDraft>(() =>
    searchDraftFrom(install.search, install.extractMedia),
  );
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const savedKey = JSON.stringify({ search: install.search, extractMedia: install.extractMedia });
  useEffect(() => {
    setDraft(searchDraftFrom(install.search, install.extractMedia));
    setShowKey(false);
  }, [savedKey]);

  const problems = searchProblems(draft);
  const dirty = searchDirty(draft, install.search, install.extractMedia);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);

  const locked = (field: string) => isLocked(install.locked, field);
  const problem = (key: string, changed: boolean) =>
    changed && problems.includes(key) ? t(key) : null;
  const edit = (patch: Partial<SearchDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
    setError(null);
  };

  const save = async () => {
    const patch = searchPatch(draft, install.search, install.extractMedia);
    if (!patch) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveSettings(patch);
      onSaved(next);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError(errorMessage(err, t("settings.saveFailed")));
    } finally {
      setSaving(false);
    }
  };

  const trial = async () => {
    setProbing(true);
    setError(null);
    setMessage(null);
    try {
      const result = await probeSearch(searchPatch(draft, install.search, install.extractMedia)?.search ?? {});
      setMessage(
        result.count > 0
          ? t("settings.search.testOk", { count: result.count })
          : t("settings.search.testNone"),
      );
    } catch (err) {
      setError(errorMessage(err, t("settings.search.testFailed")));
    } finally {
      setProbing(false);
    }
  };

  const searching = draft.provider !== "none";
  const keyLocked = locked("search.apiKey");

  return (
    <SettingsSection title={t("settings.search.title")} intro={t("settings.search.intro")}>
      <Field
        label={t("settings.search.provider")}
        locked={locked("search.provider")}
      >
        <select
          className={cn(inputClass, "h-8 text-sm")}
          value={draft.provider}
          disabled={locked("search.provider")}
          onChange={(event) => edit({ provider: event.target.value as SearchProvider })}
        >
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {t(`settings.search.provider_${provider}`)}
            </option>
          ))}
        </select>
      </Field>

      {searching ? (
        <FieldGroup title={t("settings.search.groupDetails")}>
          <Field
            label={t("settings.search.apiKey")}
            hint={
              draft.clearApiKey
                ? t("settings.search.apiKeyClearing")
                : install.search.apiKeySet
                  ? t("settings.search.apiKeyKeep")
                  : t("settings.search.apiKeyMissing")
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
            {install.search.apiKeySet && !keyLocked ? (
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
            label={t("settings.search.country")}
            hint={t("settings.search.countryHint")}
            error={problem(
              "settings.invalid.country",
              draft.country.trim().toUpperCase() !== install.search.country.trim().toUpperCase(),
            )}
            locked={locked("search.country")}
          >
            <Input
              value={draft.country}
              disabled={locked("search.country")}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ country: event.target.value })}
            />
          </Field>
          <Field
            label={t("settings.search.lang")}
            hint={t("settings.search.langHint")}
            error={problem(
              "settings.invalid.lang",
              draft.searchLang.trim() !== install.search.searchLang,
            )}
            locked={locked("search.searchLang")}
          >
            <Input
              value={draft.searchLang}
              disabled={locked("search.searchLang")}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ searchLang: event.target.value })}
            />
          </Field>
          <Field
            label={t("settings.search.uiLang")}
            hint={t("settings.search.uiLangHint")}
            error={problem(
              "settings.invalid.uiLang",
              draft.uiLang.trim() !== install.search.uiLang,
            )}
            locked={locked("search.uiLang")}
          >
            <Input
              value={draft.uiLang}
              disabled={locked("search.uiLang")}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => edit({ uiLang: event.target.value })}
            />
          </Field>
          <Field
            label={t("settings.search.timeout")}
            error={problem(
              "settings.invalid.timeout",
              numberDirty(draft.timeoutSeconds, install.search.timeoutSeconds),
            )}
            locked={locked("search.timeoutSeconds")}
          >
            <Input
              value={draft.timeoutSeconds}
              inputMode="decimal"
              disabled={locked("search.timeoutSeconds")}
              onChange={(event) => edit({ timeoutSeconds: event.target.value })}
            />
          </Field>
        </FieldGroup>
      ) : null}

      <div className="flex items-start gap-2">
        <Checkbox
          className="mt-0.5"
          checked={draft.extractMedia}
          disabled={locked("extractMedia")}
          aria-label={t("settings.search.extractMedia")}
          onCheckedChange={(checked) => edit({ extractMedia: checked === true })}
        />
        <span className="flex flex-col gap-0.5">
          <span className="text-sm">{t("settings.search.extractMedia")}</span>
          <span className="text-xs leading-relaxed text-muted-foreground">
            {locked("extractMedia")
              ? t("settings.locked")
              : t("settings.search.extractMediaHint")}
          </span>
        </span>
      </div>

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
          disabled={!searchCanProbe(draft, install.search) || probing || saving}
          onClick={() => void trial()}
        >
          {probing ? t("settings.testing") : t("settings.search.test")}
        </Button>
      </SaveRow>
    </SettingsSection>
  );
}
