import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, PenLine, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWritingState } from "@/components/writing/use-writing-state";
import type { WritingSearch } from "@/lib/api/writing";
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
  // Attention is derived, as the spec says: a failed search is one of its
  // sources, not a second row kept beside it.
  const attention: { kind?: unknown; message?: unknown }[] = [
    ...state.attention,
    ...state.searches
      .filter((search) => search.status === "failed")
      .map((search) => ({
        kind: "fetch_failed",
        target: search.id ?? "",
        message: search.error ?? "",
      })),
  ];

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
        <Searches searches={state.searches} empty={t("writing.empty")} />
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
        <Card
          title={t("writing.attention")}
          count={attention.length}
          className="md:col-span-2"
        >
          {attention.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("writing.noAttention")}
            </p>
          ) : (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {attention.map((item, index) => (
                <li key={index}>
                  {String(item.message ?? item.kind ?? "")}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </article>
  );
}

function Searches({
  searches,
  empty,
}: {
  searches: WritingSearch[];
  empty: string;
}) {
  const { t } = useTranslation();
  const latest = searches[searches.length - 1];

  return (
    <Card title={t("writing.areas.searches")} count={searches.length}>
      {!latest ? (
        <p className="mt-2 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <div className="mt-2 space-y-2">
          <p className="text-[11px] text-muted-foreground">
            {t("writing.search.query", { query: latest.query ?? "" })}
          </p>
          {latest.status === "failed" ? (
            <p className="text-xs text-destructive">
              {t("writing.search.failed")}
            </p>
          ) : (latest.hits ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("writing.search.noHits")}
            </p>
          ) : (
            <ul className="space-y-2">
              {(latest.hits ?? []).map((hit, index) => (
                <li key={hit.id ?? index}>
                  <p className="text-xs font-medium">
                    <span className="mr-1 text-muted-foreground">
                      #{hit.rank ?? index + 1}
                    </span>
                    {hit.title ?? hit.url ?? ""}
                  </p>
                  {hit.snippet ? (
                    <p className="text-[11px] text-muted-foreground">
                      {hit.snippet}
                    </p>
                  ) : null}
                  {hit.url ? (
                    <p className="truncate text-[10px] text-muted-foreground">
                      {hit.url}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
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
    <Card title={title} count={count}>
      <p className="mt-2 text-xs text-muted-foreground">
        {count ? (detail ?? "") : empty}
      </p>
    </Card>
  );
}

function Card({
  title,
  count,
  className = "",
  children,
}: {
  title: string;
  count: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`rounded-md border bg-card p-3 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium">{title}</h2>
        <span className="text-[11px] text-muted-foreground">{count}</span>
      </div>
      {children}
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
