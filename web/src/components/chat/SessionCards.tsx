import { useTranslation } from "react-i18next";
import {
  FileText,
  Frame,
  Globe,
  Layers,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";
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
  onReviewTask,
}: {
  observations: ObservationPayload[];
  /** Opening a wiki page; absent leaves a card inert. */
  onOpen?: (path: string) => void;
  /**
   * Opening the writing-task panel on the request this card shows. A write
   * request is the person's gate: the chat card is a pointer to it, and the
   * Approve/Reject buttons live where the shared task view draws them.
   */
  onReviewTask?: () => void;
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
            {row.action === "review" && onReviewTask ? (
              <button
                type="button"
                onClick={onReviewTask}
                className="shrink-0 rounded border px-1 text-[10px] text-cite hover:underline"
              >
                {t("chat.reviewTask")}
              </button>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

type Row = {
  key: string;
  kind: "page" | "search" | "web" | "material" | "frame" | "approval" | "other";
  label: string;
  /** The page's file, the result's url or the material's id, for the tooltip. */
  path: string | null;
  /** What opening this row means to the rest of the app: a page slug. */
  open: string | null;
  note: { key: string; count?: number } | null;
  /** A write request row points at the task panel, where the gate is decided. */
  action?: "review";
};

const ICONS = {
  page: FileText,
  search: Search,
  web: Globe,
  material: Layers,
  frame: Frame,
  approval: ShieldCheck,
  other: Wrench,
} as const;

/** The cards for a run, one per place, in the order it was looked at. */
export function cardRows(observations: ObservationPayload[]): Row[] {
  const rows: Row[] = [];
  const seen = new Set<string>();
  for (const observation of observations) {
    if (observation.tool === "web_search") {
      webSearchRows(observation, rows, seen);
      continue;
    }
    if (observation.tool === "pick_card") {
      materialRows(observation, rows, seen);
      continue;
    }
    if (observation.tool === "compose_page" || observation.request) {
      // A compose with no answer at all (a refusal, a handler error) falls
      // through to the generic row, which says it failed.
      if (writeRequestRows(observation, rows, seen)) continue;
    }
    if (observation.frames && observation.frames.length > 0) {
      frameRows(observation, rows, seen);
      continue;
    }
    if (observation.materials && observation.materials.length > 0) {
      materialRows(observation, rows, seen);
      continue;
    }
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

/**
 * A web search is two things: the query that ran, and the cards it brought
 * back. The query row says how many arrived (or that the search is still
 * running); each result is its own row, named so a reader can recognize it.
 */
function webSearchRows(
  observation: ObservationPayload,
  rows: Row[],
  seen: Set<string>,
): void {
  const query = (observation.query || observation.title || "").trim();
  const headerKey = `web_search:${query}:${observation.rev ?? ""}`;
  if (!seen.has(headerKey)) {
    seen.add(headerKey);
    rows.push({
      key: headerKey,
      kind: "search",
      label: query || observation.tool,
      path: null,
      open: null,
      note: noteFor(observation),
    });
  }
  for (const card of observation.cards ?? []) {
    const url = card.url?.trim() || "";
    const label = (card.title || url || card.site || "").trim();
    if (!label) continue;
    const key = `web:${url || label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      kind: "web",
      label,
      path: url || null,
      open: null,
      note: null,
    });
  }
}

/**
 * A pick (or an inspect of the materials) is one row per material: what it is,
 * and how much of it there is. A pick still running has no material yet, so it
 * is one row named by the card or the tool.
 */
function materialRows(
  observation: ObservationPayload,
  rows: Row[],
  seen: Set<string>,
): void {
  const materials = observation.materials ?? [];
  if (materials.length === 0) {
    const label = (
      observation.title ||
      observation.query ||
      observation.target ||
      observation.tool
    ).trim();
    if (!label) return;
    const key = `pick:${label}:${observation.rev ?? ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({
      key,
      kind: "material",
      label,
      path: null,
      open: null,
      note: noteFor(observation),
    });
    return;
  }
  for (const material of materials) {
    const label = (material.title || material.id || "").trim();
    if (!label) continue;
    const key = `material:${material.id || label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      kind: "material",
      label,
      path: material.id || material.url || null,
      open: null,
      note:
        material.blockCount > 0
          ? { key: "chat.looked.blocks", count: material.blockCount }
          : null,
    });
  }
}

/**
 * A compose_page is the page it proposes or the page it wrote.
 *
 * Without an answer it is the same request the task's confirmation panel
 * shows: the page and its sections, waiting for a person. Once answered it is
 * one row saying written/skipped/failed (or still writing) with the page's
 * identity. The diff stays in the task's result view, not in the chat card.
 */
function writeRequestRows(
  observation: ObservationPayload,
  rows: Row[],
  seen: Set<string>,
): boolean {
  const request = observation.request;
  if (request) {
    const spec = request.spec;
    const title = (spec?.title || spec?.slug || observation.title || "").trim();
    const key = `compose:${request.frameId}:${observation.rev ?? ""}:${title}`;
    if (seen.has(key)) return true;
    seen.add(key);
    rows.push({
      key,
      kind: "approval",
      label: title || observation.tool,
      path: null,
      open: null,
      note:
        observation.status === "approved"
          ? { key: "chat.looked.approved" }
          : { key: "chat.looked.needsApproval" },
      // The panel is the one place a person approves or rejects: the card
      // opens and refreshes it instead of growing a second decision path.
      action:
        observation.status === "approved" ? undefined : "review",
    });
    for (const [index, section] of (spec?.sections ?? []).entries()) {
      const label = (section.title || "").trim();
      if (!label) continue;
      rows.push({
        key: `${key}:section:${index}`,
        kind: "approval",
        label,
        path: null,
        open: null,
        note:
          section.blocks.length > 0
            ? { key: "chat.looked.blocks", count: section.blocks.length }
            : null,
      });
    }
    return true;
  }
  const status = (observation.status ?? "").trim();
  if (
    status !== "written" &&
    status !== "skipped" &&
    status !== "failed" &&
    status !== "requested" &&
    status !== "running"
  ) {
    return false;
  }
  const title = (
    observation.title ||
    observation.slug ||
    observation.path ||
    ""
  ).trim();
  if (!title) return false;
  const key = `compose:${status}:${observation.rev ?? ""}:${
    observation.path ?? title
  }`;
  if (seen.has(key)) return true;
  seen.add(key);
  rows.push({
    key,
    kind: "page",
    label: title,
    path: observation.path?.trim() || null,
    // Only a written page exists to open; a skipped or failed one does not.
    open:
      status === "written"
        ? observation.slug?.trim() || null
        : null,
    note:
      status === "written"
        ? { key: "chat.looked.written" }
        : status === "skipped"
          ? { key: "chat.looked.writeSkipped" }
          : status === "failed"
            ? { key: "chat.looked.writeFailed" }
            : { key: "chat.looked.writing" },
  });
  return true;
}

/**
 * A frame is one row per frame: the material it marks and how much of that
 * material it covers. Which block ids it names stays in the reader's view.
 */
function frameRows(
  observation: ObservationPayload,
  rows: Row[],
  seen: Set<string>,
): void {
  for (const frame of observation.frames ?? []) {
    const docId = (frame.docId || "").trim();
    const label = docId || frame.id || observation.tool;
    if (!label) continue;
    const key = `frame:${frame.id || docId}:${observation.rev ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      kind: "frame",
      label,
      path: docId || null,
      open: null,
      note:
        frame.blockCount > 0
          ? { key: "chat.looked.blocks", count: frame.blockCount }
          : null,
    });
  }
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
  if (observation.status === "requested" || observation.status === "running") {
    return {
      key:
        observation.tool === "pick_card"
          ? "chat.looked.picking"
          : observation.tool === "compose_page" || observation.tool === "inspect"
            ? "chat.looked.writing"
            : "chat.looked.searching",
    };
  }
  // An inspect("result") reads the last page write's outcome.
  if (observation.status === "written") return { key: "chat.looked.written" };
  if (observation.status === "skipped") return { key: "chat.looked.writeSkipped" };
  if (observation.status === "failed") return { key: "chat.looked.writeFailed" };
  if (observation.omitted) return { key: "chat.looked.omitted" };
  if (observation.truncated) return { key: "chat.looked.truncated" };
  if (typeof observation.hits === "number") {
    return { key: "chat.looked.hits", count: observation.hits };
  }
  return null;
}
