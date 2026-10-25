import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/input";
import { SaveRow, SettingsSection } from "@/components/settings/fields";
import { fetchBrief, saveBrief, type ProjectBrief } from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import i18n from "@/i18n";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * What this wiki is for.
 *
 * The words are per project, so the switcher lives here: the sidebar that
 * usually holds it is hidden while settings are open. Creating and deleting a
 * project stays there, because those are not edits of the description.
 */
export function ProjectSection({ onDirty }: { onDirty: (dirty: boolean) => void }) {
  const { t } = useTranslation();
  const projects = useWorkspaceStore((state) => state.projects);
  const current = useWorkspaceStore((state) => state.current);
  const select = useWorkspaceStore((state) => state.select);
  const openModule = useLayoutStore((state) => state.openModule);

  const [brief, setBrief] = useState<ProjectBrief | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!current) {
      setBrief(null);
      setDraft("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setBrief(null);
    setDraft("");
    void fetchBrief(current)
      .then((loaded) => {
        if (cancelled) return;
        setBrief(loaded);
        setDraft(loaded.text);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(errorMessage(err, i18n.t("settings.project.loadFailed")));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [current]);

  const dirty = brief !== null && draft !== brief.text;
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);

  const tooLong = brief !== null && draft.trim().length > brief.maxChars;

  const save = async () => {
    if (!current || !brief || tooLong) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveBrief(current, draft);
      setBrief(next);
      setDraft(next.text);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError(errorMessage(err, t("settings.saveFailed")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection title={t("settings.project.title")} intro={t("settings.project.intro")}>
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">{t("project.label")}</span>
        <select
          className={cn(inputClass, "h-8 text-sm")}
          value={current ?? ""}
          disabled={!current || dirty}
          onChange={(event) => void select(event.target.value)}
        >
          {current === null ? <option value="">{t("project.none")}</option> : null}
          {projects.map((project) => (
            <option key={project.name} value={project.name}>
              {project.name}
            </option>
          ))}
        </select>
        <span className="text-xs leading-relaxed text-muted-foreground">
          {dirty ? t("settings.project.switchBlocked") : t("settings.project.manage")}
        </span>
      </label>

      {!current ? (
        <p className="text-sm text-muted-foreground">{t("settings.project.noProject")}</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">{t("settings.loading")}</p>
      ) : loadError ? (
        <p className="text-sm text-destructive">{loadError}</p>
      ) : brief ? (
        <>
          {brief.source === "default" ? (
            <p className="text-sm text-muted-foreground">{t("settings.project.usingDefault")}</p>
          ) : null}
          {brief.source === "legacy" ? (
            <p className="text-sm text-muted-foreground">{t("settings.project.legacy")}</p>
          ) : null}
          <textarea
            className={cn(inputClass, "h-auto min-h-48 py-2 leading-relaxed")}
            value={draft}
            placeholder={t("settings.project.placeholder")}
            aria-invalid={tooLong || undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setMessage(null);
              setError(null);
            }}
          />
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-2">
              {brief.source === "default" ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setDraft(brief.defaultText);
                    setMessage(null);
                  }}
                >
                  {t("settings.project.startFromDefault")}
                </Button>
              ) : null}
              {brief.source === "project" ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setDraft("");
                    setMessage(null);
                  }}
                >
                  {t("settings.project.clear")}
                </Button>
              ) : null}
            </div>
            <span
              className={cn(
                "text-xs tabular-nums text-muted-foreground",
                tooLong && "text-destructive",
              )}
            >
              {draft.trim().length} / {brief.maxChars}
            </span>
          </div>
          {tooLong ? (
            <p className="text-xs text-destructive">
              {t("settings.project.tooLong", { limit: brief.maxChars })}
            </p>
          ) : null}
          <SaveRow
            canSave={dirty && !tooLong}
            saving={saving}
            onSave={() => void save()}
            message={message}
            error={error}
          />
        </>
      ) : null}

      <p className="text-sm text-muted-foreground">
        {t("settings.project.chatLimits")}{" "}
        <button
          type="button"
          className="underline-offset-2 hover:underline"
          onClick={() => openModule("chat")}
        >
          {t("settings.project.openChat")}
        </button>
      </p>
    </SettingsSection>
  );
}
