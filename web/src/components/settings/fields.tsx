import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

export function SettingsSection({
  title,
  intro,
  children,
}: {
  title: string;
  intro: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-5">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{intro}</p>
      </div>
      {children}
    </section>
  );
}

export function FieldGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      {children}
    </div>
  );
}

/**
 * One labelled control.
 *
 * The lock note and the error replace the hint: both are the reason the value
 * cannot be taken as typed, and showing all three asks the reader to pick.
 */
export function Field({
  label,
  hint,
  error,
  locked,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  locked?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {locked ? (
        <span className="text-xs leading-relaxed text-muted-foreground">{t("settings.locked")}</span>
      ) : null}
      {error ? <span className="text-xs leading-relaxed text-destructive">{error}</span> : null}
      {hint && !error && !locked ? (
        <span className="text-xs leading-relaxed text-muted-foreground">{hint}</span>
      ) : null}
    </label>
  );
}

export function SaveRow({
  canSave,
  saving,
  onSave,
  message,
  error,
  children,
}: {
  canSave: boolean;
  saving: boolean;
  onSave: () => void;
  message: string | null;
  error: string | null;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-3">
      <Button type="button" disabled={!canSave || saving} onClick={onSave}>
        {saving ? t("settings.saving") : t("settings.save")}
      </Button>
      {children}
      {message ? (
        <span role="status" className="text-xs text-muted-foreground">
          {message}
        </span>
      ) : null}
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  );
}
