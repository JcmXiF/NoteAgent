import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, PenLine, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWritingState } from "@/components/writing/use-writing-state";
import type { PaneSlot } from "@/stores/layout-store";

/**
 * The project's writing state, read-only for now.
 *
 * It is the one place the four areas the agent works on are visible: searches,
 * selected pages, frames and the draft, with what still needs a person. Editing
 * and the save lock arrive with the save slice; this screen only shows the
 * truth and its version.
 */
export function WritingStateView(_props: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const { project, state, error, load } = useWritingState();

  if (!project) {
    return <Notice title={t("writing.noProject")} />;
  }

  if (error) {
    return (
      <Notice
        title={t("writing.error")}
        detail={error}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load(project, true)}
          >
            <RefreshCw /> {t("writing.retry")}
          </Button>
        }
      />
    );
  }

  if (!state) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("writing.loading")}
      </div>
    );
  }

  const pages = state.draft.pages ?? [];
  const sections = pages.reduce(
    (total, page) =>
      total + (Array.isArray(page.sections) ? page.sections.length : 0),
    0,
  );
  const writer = state.writer
    ? state.writer.id || state.writer.kind || t("writing.noWriter")
    : t("writing.noWriter");

  return (
    <article className="flex h-full min-h-0 flex-col overflow-auto">
      <header className="flex shrink-0 items-center gap-2 border-b px-4 py-3">
        <PenLine className="size-4 text-muted-foreground" />
        <h1 className="text-sm font-medium">{t("writing.title")}</h1>
        <span className="text-[11px] text-muted-foreground">
          {t("writing.version", { version: state.stateVersion })}
        </span>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {t("writing.writer", { writer })}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => void load(project, true)}
          aria-label={t("writing.retry")}
        >
          <RefreshCw />
        </Button>
      </header>
      <div className="grid gap-3 p-4 md:grid-cols-2">
        <Section
          title={t("writing.areas.searches")}
          count={state.searches.length}
          empty={t("writing.empty")}
        />
        <Section
          title={t("writing.areas.selectedPages")}
          count={state.selectedPages.length}
          empty={t("writing.empty")}
        />
        <Section
          title={t("writing.areas.frames")}
          count={state.frames.length}
          empty={t("writing.empty")}
        />
        <Section
          title={t("writing.areas.draft")}
          count={sections}
          detail={t("writing.draftCount", {
            pages: pages.length,
            sections,
          })}
          empty={t("writing.empty")}
        />
        <section className="rounded-md border bg-card p-3 md:col-span-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-medium">{t("writing.attention")}</h2>
            <span className="text-[11px] text-muted-foreground">
              {state.attention.length}
            </span>
          </div>
          {state.attention.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("writing.noAttention")}
            </p>
          ) : (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {state.attention.map((item, index) => (
                <li key={index}>
                  {String(item.message ?? item.kind ?? "")}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </article>
  );
}

function Section({
  title,
  count,
  detail,
  empty,
}: {
  title: string;
  count: number;
  detail?: string;
  empty: string;
}) {
  return (
    <section className="rounded-md border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium">{title}</h2>
        <span className="text-[11px] text-muted-foreground">{count}</span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {count ? (detail ?? "") : empty}
      </p>
    </section>
  );
}

function Notice({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      {detail ? (
        <p className="max-w-md text-xs text-muted-foreground">{detail}</p>
      ) : null}
      {action}
    </div>
  );
}
