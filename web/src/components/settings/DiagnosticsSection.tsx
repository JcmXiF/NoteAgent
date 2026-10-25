import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/settings/fields";
import {
  checkForUpdate,
  fetchDiagnostics,
  fetchDiagnosticsLog,
  type Diagnostics,
  type DiagnosticsLog,
  type UpdateCheck,
} from "@/lib/api/diagnostics";
import { errorMessage } from "@/lib/api/client";
import { openInThisBrowser } from "@/lib/browse";
import i18n from "@/i18n";

/**
 * What this copy can say about itself, for the moment something is wrong.
 *
 * The screen is read, not operated: nothing here changes the state it describes.
 * The one action that leaves the machine is the update check, and it leaves only
 * when the button is pressed — an app that phones home from a settings page is
 * saying something about itself that this one does not want to say.
 */

/** Warnings this build knows how to word; a newer service may name others. */
const KNOWN_WARNINGS = new Set(["noProject", "noModel", "noLog"]);

function uptimeText(seconds: number, t: TFunction) {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return t("settings.diagnostics.uptime.seconds", { count: total });
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return t("settings.diagnostics.uptime.minutes", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return t("settings.diagnostics.uptime.hours", { count: hours, minutes: minutes % 60 });
  }
  return t("settings.diagnostics.uptime.days", {
    count: Math.floor(hours / 24),
    hours: hours % 24,
  });
}

function Row({
  label,
  children,
  mono,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-xs break-all" : "text-sm"}>{children}</dd>
    </div>
  );
}

export function DiagnosticsSection({
  onOpenSettings,
}: {
  /** Where the warnings that are about a setting point the reader. */
  onOpenSettings: (section: "model" | "search") => void;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<Diagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [log, setLog] = useState<DiagnosticsLog | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [logBusy, setLogBusy] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  const [update, setUpdate] = useState<UpdateCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const [copied, setCopied] = useState<string | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchDiagnostics());
      setError(null);
    } catch (err) {
      setError(errorMessage(err, t("settings.diagnostics.loadFailed")));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  // The "copied" answer is about the click that just happened, not a state the row
  // keeps; two seconds is long enough to read and short enough not to lie.
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(null), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async (text: string) => {
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
    } catch {
      setCopyFailed(true);
    }
  };

  const toggleLog = async () => {
    if (logOpen) {
      setLogOpen(false);
      return;
    }
    setLogOpen(true);
    if (log !== null) return;
    setLogBusy(true);
    try {
      setLog(await fetchDiagnosticsLog());
      setLogError(null);
    } catch (err) {
      setLogError(errorMessage(err, t("settings.diagnostics.logFailed")));
    } finally {
      setLogBusy(false);
    }
  };

  const askUpdate = async () => {
    setChecking(true);
    setUpdateError(null);
    try {
      setUpdate(await checkForUpdate());
    } catch (err) {
      setUpdate(null);
      setUpdateError(errorMessage(err, t("settings.diagnostics.check.failed")));
    } finally {
      setChecking(false);
    }
  };

  const copyButton = (path: string) => (
    <Button type="button" variant="ghost" size="sm" onClick={() => void copy(path)}>
      {copied === path
        ? t("settings.diagnostics.copied")
        : t("settings.diagnostics.copyPath")}
    </Button>
  );

  return (
    <SettingsSection
      title={t("settings.diagnostics.title")}
      intro={t("settings.diagnostics.intro")}
    >
      {loading && !data ? (
        <p className="text-sm text-muted-foreground">{t("settings.diagnostics.loading")}</p>
      ) : null}

      {error ? (
        <div className="flex flex-col items-start gap-2">
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
          <Button type="button" variant="outline" onClick={() => void load()}>
            {t("common.retry")}
          </Button>
        </div>
      ) : null}

      {data ? (
        <>
          <dl className="flex flex-col gap-3">
            <Row label={t("settings.diagnostics.versionLabel")}>
              <span className="font-mono text-xs">{data.version}</span>
            </Row>
            <Row label={t("settings.diagnostics.uptimeLabel")}>
              {uptimeText(data.uptimeSeconds, t)}
            </Row>
            <Row label={t("settings.diagnostics.addressLabel")}>
              {data.address ? (
                <span className="font-mono text-xs">{data.address.url}</span>
              ) : (
                <span className="text-muted-foreground">
                  {t("settings.diagnostics.addressNone")}
                </span>
              )}
            </Row>
            <Row label={t("settings.diagnostics.restartsLabel")}>
              {data.supervised
                ? t("settings.diagnostics.supervised")
                : t("settings.diagnostics.unsupervised")}
              {data.restarts > 0
                ? ` · ${t("settings.diagnostics.restartsCount", { count: data.restarts })}`
                : ""}
              {data.lastExit
                ? ` · ${t("settings.diagnostics.lastExit", {
                    when: new Date(data.lastExit.at * 1000).toLocaleString(i18n.language),
                    code: data.lastExit.code,
                  })}`
                : ""}
            </Row>
            <Row label={t("settings.diagnostics.projectsLabel")}>
              {data.activeProject
                ? t("settings.diagnostics.projectsActive", {
                    count: data.projectCount,
                    name: data.activeProject,
                  })
                : t("settings.diagnostics.projectsNone", { count: data.projectCount })}
            </Row>
            <Row label={t("settings.diagnostics.dataLabel")} mono>
              <span className="flex flex-wrap items-center gap-2">
                {data.dataRoot}
                {copyButton(data.dataRoot)}
              </span>
            </Row>
          </dl>

          {data.warnings.length > 0 ? (
            <div className="flex flex-col gap-1.5 rounded-md border border-dashed p-3">
              <span className="text-xs font-medium">
                {t("settings.diagnostics.warnTitle")}
              </span>
              {data.warnings
                .filter((code) => KNOWN_WARNINGS.has(code))
                .map((code) => (
                  <div key={code} className="flex flex-wrap items-baseline gap-2">
                    <p className="text-xs text-muted-foreground">
                      {t(`settings.diagnostics.warn.${code}`)}
                    </p>
                    {code === "noModel" ? (
                      <button
                        type="button"
                        className="text-xs underline underline-offset-2"
                        onClick={() => onOpenSettings("model")}
                      >
                        {t("settings.diagnostics.warn.goModel")}
                      </button>
                    ) : null}
                  </div>
                ))}
            </div>
          ) : null}

          <div className="flex flex-col gap-2">
            <span className="text-xs text-muted-foreground">
              {t("settings.diagnostics.logLabel")}
            </span>
            {data.logs ? (
              <>
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs break-all">{data.logs.path}</span>
                  {copyButton(data.logs.path)}
                </span>
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={logBusy}
                    onClick={() => void toggleLog()}
                  >
                    {logOpen
                      ? t("settings.diagnostics.logClose")
                      : t("settings.diagnostics.logOpen")}
                  </Button>
                </div>
                {logOpen ? (
                  logBusy && !log ? (
                    <p className="text-xs text-muted-foreground">
                      {t("settings.diagnostics.loading")}
                    </p>
                  ) : logError ? (
                    <p role="alert" className="text-xs text-destructive">
                      {logError}
                    </p>
                  ) : log && log.lines.length > 0 ? (
                    <pre className="scroll-thin max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all">
                      {log.lines.join("\n")}
                    </pre>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("settings.diagnostics.logEmpty")}
                    </p>
                  )
                ) : null}
              </>
            ) : (
              <span className="text-sm text-muted-foreground">
                {t("settings.diagnostics.logNone")}
              </span>
            )}
          </div>

          <div className="flex flex-col gap-2 border-t pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={checking}
                onClick={() => void askUpdate()}
              >
                {checking
                  ? t("settings.diagnostics.check.checking")
                  : t("settings.diagnostics.check.button")}
              </Button>
              <span role="status" className="text-xs text-muted-foreground">
                {update === null
                  ? ""
                  : !update.configured
                    ? t("settings.diagnostics.check.unconfigured", { version: update.current })
                    : update.available
                      ? t("settings.diagnostics.check.available", {
                          latest: update.latest,
                          current: update.current,
                        })
                      : t("settings.diagnostics.check.upToDate", { version: update.current })}
              </span>
            </div>
            {update?.configured && update.available && update.notes ? (
              <p className="text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground">
                {update.notes}
              </p>
            ) : null}
            {update?.configured && update.available && update.url ? (
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => openInThisBrowser(update.url)}
                >
                  {t("settings.diagnostics.check.download")}
                </Button>
              </div>
            ) : null}
            {updateError ? (
              <p role="alert" className="text-xs text-destructive">
                {updateError}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button
              type="button"
              variant="outline"
              disabled={loading}
              onClick={() => void load()}
            >
              {t("settings.diagnostics.refresh")}
            </Button>
            {copyFailed ? (
              <span role="alert" className="text-xs text-destructive">
                {t("settings.diagnostics.copyFailed")}
              </span>
            ) : null}
          </div>
        </>
      ) : null}
    </SettingsSection>
  );
}
