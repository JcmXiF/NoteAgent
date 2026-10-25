import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Loader2,
  RotateCcw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useActivityStore, type ActivityItem } from "@/stores/activity-store";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import type { ModuleId } from "@/modules";

/** Where "go to the related view" should land, per job kind. */
const MODULE_FOR_KIND: Partial<Record<ActivityItem["kind"], ModuleId>> = {
  search: "browse",
  fetch: "sources",
  save: "sources",
  plan: "docs",
  compose: "docs",
  chat: "chat",
  "deep-search": "graph",
};

function PhaseIcon({ phase }: { phase: ActivityItem["phase"] }) {
  if (phase === "running") {
    return <Loader2 className="size-3 shrink-0 animate-spin text-primary" />;
  }
  if (phase === "error") {
    return <AlertCircle className="size-3 shrink-0 text-destructive" />;
  }
  if (phase === "cancelled") {
    return <Square className="size-3 shrink-0 text-muted-foreground" />;
  }
  return <CheckCircle2 className="size-3 shrink-0 text-success" />;
}

/**
 * What the jobs amount to right now, for the one line that stands for them.
 *
 * The line is the whole of the panel when it is closed, so it says the most useful
 * thing there is: what is running and what it is doing, then what went wrong, then
 * that everything finished.
 */
type ActivityHeadline =
  | { kind: "running"; text: string }
  | { kind: "failed"; count: number }
  | { kind: "done" }
  | { kind: "idle" };

export function activityHeadline(items: ActivityItem[]): ActivityHeadline {
  const running = items.find((item) => item.phase === "running");
  if (running) return { kind: "running", text: running.message || running.title };
  const failed = items.filter((item) => item.phase === "error").length;
  if (failed > 0) return { kind: "failed", count: failed };
  return items.length > 0 ? { kind: "done" } : { kind: "idle" };
}

/**
 * Everything that has run, and how it went.
 *
 * Long-running work — search, fetch, save, plan, compile, chat, deep search —
 * registers here, so it stays visible after the reader switches to another view.
 * The list is shared by both places it appears (the overlay and the sidebar), so
 * the two cannot drift apart; its heading and its "clear" action belong to whoever
 * puts it on screen.
 */
export function JobsList() {
  const { t } = useTranslation();
  const items = useActivityStore((state) => state.items);

  if (items.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scroll-thin">
        <p className="px-3 py-2 text-[11px] text-muted-foreground">{t("activity.empty")}</p>
      </div>
    );
  }

  return (
    <ul className="flex min-h-0 flex-1 flex-col gap-px overflow-y-auto pb-2 scroll-thin">
      {items.map((item) => (
        <JobRow key={item.id} item={item} />
      ))}
    </ul>
  );
}

/** The one line that stands for the jobs where the list is folded away. */
export function JobsSummary() {
  const { t } = useTranslation();
  const items = useActivityStore((state) => state.items);
  const headline = activityHeadline(items);

  return (
    <>
      {headline.kind === "running" ? (
        <Loader2 className="size-3 shrink-0 animate-spin" />
      ) : headline.kind === "failed" ? (
        <AlertCircle className="size-3 shrink-0 text-destructive" />
      ) : (
        <CheckCircle2 className="size-3 shrink-0 text-success/70" />
      )}
      <span className="min-w-0 flex-1 truncate text-left text-[11px] text-muted-foreground">
        {headline.kind === "running"
          ? headline.text
          : headline.kind === "failed"
            ? t("activity.failedCount", { count: headline.count })
            : headline.kind === "done"
              ? t("activity.idleDone")
              : t("activity.idle")}
      </span>
      {items.length > 0 ? (
        <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums opacity-70">
          {items.length}
        </span>
      ) : null}
    </>
  );
}

/** Drops everything that is no longer running. */
export function ClearFinishedButton() {
  const { t } = useTranslation();
  const items = useActivityStore((state) => state.items);
  const clearFinished = useActivityStore((state) => state.clearFinished);

  return (
    <IconTip label={t("activity.clearFinished")}>
      <Button
        variant="ghost"
        size="icon-xs"
        disabled={items.every((item) => item.phase === "running")}
        onClick={clearFinished}
      >
        <Trash2 />
      </Button>
    </IconTip>
  );
}

function JobRow({ item }: { item: ActivityItem }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const cancel = useActivityStore((state) => state.cancel);
  const remove = useActivityStore((state) => state.remove);
  const openModule = useLayoutStore((state) => state.openModule);
  const go = useNavStore((state) => state.go);

  const targetModule = item.module ?? MODULE_FOR_KIND[item.kind];
  // A stopped job must not keep claiming it is still working.
  const message = item.phase === "cancelled" ? t("activity.cancelled") : item.message;

  return (
    <li className="group px-3 py-1.5 text-[11px] hover:bg-accent/40">
      <div className="flex items-start gap-2">
        <span className="mt-0.5">
          <PhaseIcon phase={item.phase} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">
              {t(`activity.kind.${item.kind}`)}
            </span>
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left font-medium"
              onClick={() => setOpen((prev) => !prev)}
            >
              {item.title}
            </button>
          </div>
          {message ? <p className="mt-0.5 truncate text-muted-foreground">{message}</p> : null}
          {item.error ? <p className="mt-0.5 text-destructive">{item.error}</p> : null}

          {item.steps && item.steps.length > 0 ? (
            <ol className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground">
              {item.steps.map((step, index) => (
                <li key={step.id} className="flex items-center gap-1">
                  {index > 0 ? <span className="opacity-40">→</span> : null}
                  <span
                    className={cn(
                      step.state === "current" && "font-semibold text-foreground",
                      step.state === "done" && "text-success",
                      step.state === "blocked" && "text-destructive",
                    )}
                  >
                    {step.label}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}

          {open ? (
            <div className="mt-1 flex flex-col gap-1">
              {item.filesWritten.length > 0 ? (
                <ul className="flex flex-col gap-0.5">
                  {item.filesWritten.slice(0, 12).map((path) => (
                    <li key={path}>
                      <button
                        type="button"
                        className="truncate text-left text-cite hover:underline"
                        onClick={() => go({ kind: "docs", path }, { placement: "replace" })}
                      >
                        {path}
                      </button>
                    </li>
                  ))}
                  {item.filesWritten.length > 12 ? (
                    <li className="text-muted-foreground">
                      {t("activity.moreFiles", { count: item.filesWritten.length - 12 })}
                    </li>
                  ) : null}
                </ul>
              ) : null}
              {item.detail.length > 0 ? (
                <pre className="max-h-40 overflow-auto rounded bg-muted/60 p-1.5 text-[10px] whitespace-pre-wrap scroll-thin">
                  {item.detail.join("\n")}
                </pre>
              ) : null}
              {item.filesWritten.length === 0 && item.detail.length === 0 ? (
                <p className="text-muted-foreground">{t("activity.noDetail")}</p>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100">
          {item.phase === "running" && item.cancel ? (
            <IconTip label={t("activity.interrupt")}>
              <Button variant="ghost" size="icon-xs" onClick={() => cancel(item.id)}>
                <Square />
              </Button>
            </IconTip>
          ) : null}
          {item.phase === "error" && item.retry ? (
            <IconTip label={t("activity.retry")}>
              <Button variant="ghost" size="icon-xs" onClick={item.retry}>
                <RotateCcw />
              </Button>
            </IconTip>
          ) : null}
          {targetModule && item.phase === "done" ? (
            <IconTip label={t("activity.openModule")}>
              <Button variant="ghost" size="icon-xs" onClick={() => openModule(targetModule)}>
                <ArrowUpRight />
              </Button>
            </IconTip>
          ) : null}
          {item.phase !== "running" ? (
            <IconTip label={t("activity.remove")}>
              <Button variant="ghost" size="icon-xs" onClick={() => remove(item.id)}>
                <X />
              </Button>
            </IconTip>
          ) : null}
        </div>
      </div>
    </li>
  );
}
