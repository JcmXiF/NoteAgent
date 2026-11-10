import { useTranslation } from "react-i18next";
import { FileText, Search, Wrench } from "lucide-react";
import type { ObservationPayload } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/**
 * What the answer looked at, as a row per thing.
 *
 * The core sends one observation per tool call: a page it read, or a search it
 * ran. The model's text is not here — a row says what was touched and where it
 * is, so the reader can open the page rather than only read about it.
 */
export function SessionCards({
  observations,
  onOpen,
}: {
  observations: ObservationPayload[];
  /** Opening a wiki page; absent leaves a card inert. */
  onOpen?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const rows = cardRows(observations);
  if (rows.length === 0) return null;

  return (
    <section
      aria-label={t("chat.looked.title")}
      className="mt-1.5 mb-2 flex flex-col gap-px rounded-md border bg-muted/30 p-1.5"
    >
      {rows.map((row) => {
        const Icon = ICONS[row.kind];
        const open = row.open;
        const opens = open !== null && onOpen !== undefined;
        return (
          <div
            key={row.key}
            className="flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px]"
          >
            <Icon
              className={cn(
                "size-3 shrink-0",
                opens ? "text-cite" : "text-muted-foreground",
              )}
            />
            {open !== null && onOpen ? (
              <button
                type="button"
                onClick={() => onOpen(open)}
                className="min-w-0 flex-1 truncate text-left text-cite hover:underline"
                title={row.path ?? open}
              >
                {row.label}
              </button>
            ) : (
              <span
                className="min-w-0 flex-1 truncate text-muted-foreground"
                title={row.path ?? row.label}
              >
                {row.label}
              </span>
            )}
            {row.note ? (
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {t(row.note.key, { count: row.note.count })}
              </span>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

type Row = {
  key: string;
  kind: "page" | "search" | "other";
  label: string;
  /** The page's file, for the tooltip. */
  path: string | null;
  /** What opening this row means to the rest of the app: a page slug. */
  open: string | null;
  note: { key: string; count?: number } | null;
};

const ICONS = {
  page: FileText,
  search: Search,
  other: Wrench,
} as const;

/** The cards for a run, one per place, in the order it was looked at. */
export function cardRows(observations: ObservationPayload[]): Row[] {
  const rows: Row[] = [];
  const seen = new Set<string>();
  for (const observation of observations) {
    const path = observation.path?.trim() || null;
    const label = (
      observation.title ||
      observation.target ||
      observation.query ||
      observation.tool
    ).trim();
    if (!label) continue;
    const key = observation.path ?? observation.target ?? `${observation.tool}:${label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      kind:
        observation.tool === "search"
          ? "search"
          : observation.tool === "wiki_read" || path
            ? "page"
            : "other",
      label,
      path: observation.tool === "wiki_read" ? path : null,
      open:
        observation.tool === "wiki_read" && path !== null
          ? observation.slug?.trim() || linkTarget(observation.target)
          : null,
      note: noteFor(observation),
    });
  }
  return rows;
}

function linkTarget(target: string | undefined): string | null {
  const text = target?.trim() ?? "";
  if (!text) return null;
  const link = /^\[\[(.+?)\]\]$/.exec(text);
  return (link?.[1] ?? text).trim() || null;
}

function noteFor(observation: ObservationPayload): Row["note"] {
  if (observation.missing) return { key: "chat.looked.missing" };
  if (observation.timeout) return { key: "chat.looked.timeout" };
  if (observation.error) return { key: "chat.looked.failed" };
  if (observation.omitted) return { key: "chat.looked.omitted" };
  if (observation.truncated) return { key: "chat.looked.truncated" };
  if (typeof observation.hits === "number") {
    return { key: "chat.looked.hits", count: observation.hits };
  }
  return null;
}
