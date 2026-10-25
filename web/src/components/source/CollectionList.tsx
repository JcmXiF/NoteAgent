import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Check, HardDriveDownload, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { IconTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  collectionHeadline,
  collectionLead,
  collectionRangeLabel,
  collectionsByScope,
  isPreviewSource,
  type BlockCollection,
} from "@/lib/clips";
import { runAsActivity } from "@/lib/run";
import { isBindableWikiPage } from "@/lib/source-kind";
import { wikiPathSlug } from "@/lib/project-tree";
import { ShownError } from "@/lib/api/client";
import {
  MissingSourcesError,
  SourceNotStoredError,
  ensureSourcesOnDisk,
  missingSourceLabels,
} from "@/lib/persist-sources";
import { useAcquireStore } from "@/stores/acquire-store";
import { useClipsStore } from "@/stores/clips-store";
import { useSectionCount } from "@/stores/right-sections";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useDocsStore } from "@/stores/docs-store";
import { useNavStore } from "@/stores/nav-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * The passages chosen for compilation, grouped by the page they came from.
 *
 * Selections made during a deep search are shown apart from the everyday ones,
 * because they are not the same kind of thing: material gathered for one search is
 * planned by that search, and the everyday Planning pass leaves it alone. The two
 * are a switch rather than one list with marks on it, so what will be compiled is
 * never in doubt.
 *
 * It shares the column with the plan, so it is a flexible section that scrolls
 * internally — neither list may push the other off the panel.
 */
export function CollectionList() {
  const { t } = useTranslation();
  const collections = useClipsStore((state) => state.collections);
  const roundId = useDeepSearchStore((state) => state.session?.roundId ?? "");
  const scope = useMemo(
    () => collectionsByScope(collections, roundId),
    [collections, roundId],
  );
  // Which list the reader is looking at. Chosen by them once they choose; until
  // then a running search is what they are working on, so that is what they see.
  const [chosen, setChosen] = useState<"round" | "everyday" | null>(null);
  const view = chosen ?? (roundId || scope.leftover.length > 0 ? "round" : "everyday");

  // The heading says how many, so a folded section is not silent about them. The
  // count is the whole list, since which scope is being viewed is that section's
  // own business. Read before the empty case returns: a count is a hook, and one
  // that only runs once there is something to count is a hook that runs sometimes.
  useSectionCount("selection", collections.length);

  if (collections.length === 0) {
    return (
      <div className="flex shrink-0 flex-col gap-1.5 p-3">
        <p className="text-xs font-medium">{t("selections.empty.title")}</p>
        <p className="text-[11px] text-muted-foreground">{t("selections.empty.body")}</p>
      </div>
    );
  }

  const roundCount = scope.mine.length + scope.leftover.length;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-1 border-b px-2 py-1.5">
        <div
          role="group"
          aria-label={t("selections.scope.label")}
          className="flex gap-0.5 rounded-md bg-muted/60 p-0.5"
        >
          {(
            [
              ["round", t(roundId ? "selections.scope.mine" : "selections.scope.leftover"), roundCount],
              ["everyday", t("selections.scope.everyday"), scope.everyday.length],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              aria-pressed={view === key}
              onClick={() => setChosen(key)}
              className={cn(
                "min-w-0 flex-1 truncate rounded px-2 py-0.5 text-[11px]",
                view === key
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label} {count}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">
            {t("selections.count", { count: view === "round" ? roundCount : scope.everyday.length })}
          </span>
          {view === "everyday" ? <ScopeNote>{"selections.scope.everydayHint"}</ScopeNote> : null}
          <div className="ml-auto">
            <SaveOriginalsButton />
          </div>
        </div>
      </div>

      {view === "round" && !roundId && scope.leftover.length > 0 ? (
        <LeftoverRow round={scope.leftover.length} />
      ) : null}

      <SelectionGroups
        collections={view === "round" ? [...scope.mine, ...scope.leftover] : scope.everyday}
        // Marks the rows that do not belong to the round being run: in the round's
        // own list those are the leftovers, and saying so is the whole point of
        // showing them there. Adding a round to a round would say nothing.
        liveRoundId={roundId}
      />
    </div>
  );
}

function ScopeNote({ children }: { children: string }) {
  const { t } = useTranslation();
  return (
    <span className="min-w-0 truncate text-[10px] text-muted-foreground">{t(children)}</span>
  );
}

/**
 * What to do with selections a round left behind.
 *
 * A round can end without taking a decision about them — it is closed elsewhere, or
 * the project is opened after the fact — and until something is said they are shown
 * as if work were still in progress. Neither choice is made for the reader: keeping
 * them changes what the everyday planning will compile, and dropping them is not
 * reversible.
 */
function LeftoverRow({ round }: { round: number }) {
  const { t } = useTranslation();
  const releaseRound = useClipsStore((state) => state.releaseRound);
  const dropRound = useClipsStore((state) => state.dropRound);
  const collections = useClipsStore((state) => state.collections);
  // Derived here rather than in the selector: a selector that builds an array
  // returns a new one every render, which a store comparing by identity reads as
  // a change and re-renders on for ever.
  const owners = useMemo(
    () =>
      [...new Set(collections.map((item) => item.roundId).filter(Boolean))] as string[],
    [collections],
  );

  return (
    <div className="flex shrink-0 flex-col gap-1 border-b bg-warning/5 px-2 py-1.5">
      <p className="text-[11px]">{t("selections.leftover.title", { count: round })}</p>
      <p className="text-[10px] text-muted-foreground">{t("selections.leftover.body")}</p>
      <div className="flex items-center gap-1.5">
        {owners.map((owner) => (
          <Button key={owner} size="sm" variant="outline" onClick={() => releaseRound(owner)}>
            {t("selections.leftover.keep")}
          </Button>
        ))}
        {owners.length === 1 ? (
          <Button size="sm" variant="ghost" onClick={() => dropRound(owners[0] ?? "")}>
            {t("selections.leftover.drop")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** The selections, grouped by the source they came from. */
function SelectionGroups({
  collections,
  liveRoundId,
}: {
  collections: ReturnType<typeof useClipsStore.getState>["collections"];
  liveRoundId: string;
}) {
  const { t } = useTranslation();
  const focusedId = useClipsStore((state) => state.focusedId);
  const focusCollection = useClipsStore((state) => state.focusCollection);
  const removeCollection = useClipsStore((state) => state.removeCollection);
  const go = useNavStore((state) => state.go);

  const groups = useMemo(() => {
    const byKey = new Map<string, typeof collections>();
    for (const collection of collections) {
      const list = byKey.get(collection.sourceKey);
      if (list) list.push(collection);
      else byKey.set(collection.sourceKey, [collection]);
    }
    return [...byKey.entries()];
  }, [collections]);

  if (collections.length === 0) {
    return (
      <p className="p-3 text-[11px] text-muted-foreground">{t("selections.scope.empty")}</p>
    );
  }

  return (
    <ul className="min-h-0 flex-1 overflow-y-auto scroll-thin">
      {groups.map(([sourceKey, items]) => (
        <li key={sourceKey} className="border-b last:border-b-0">
          <div className="flex items-center gap-1 px-2 pt-1.5">
            <span className="min-w-0 flex-1 truncate text-[10px] text-muted-foreground">
              {items[0]?.sourceLabel || sourceKey}
            </span>
            {isPreviewSource(sourceKey) ? (
              <IconTip label={t("selections.storedHint")}>
                <span className="shrink-0 text-warning">
                  <AlertCircle className="size-3" />
                </span>
              </IconTip>
            ) : (
              <span className="shrink-0 text-success">
                <Check className="size-3" />
              </span>
            )}
          </div>
          <ul className="pb-1">
            {items.map((collection) => {
              const active = collection.id === focusedId;
              return (
                <li key={collection.id}>
                  <div
                    className={cn(
                      "group flex items-start gap-1 px-2 py-1",
                      active ? "bg-accent" : "hover:bg-accent/40",
                    )}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => {
                        focusCollection(collection.id);
                        go(
                          { kind: "sources", sourceKey: collection.sourceKey },
                          { placement: "beside" },
                        );
                      }}
                    >
                      <span className="flex items-center gap-1">
                        <span className="min-w-0 truncate text-[11px] font-medium">
                          {collectionHeadline(collection) || t("clips.untitled")}
                        </span>
                        {collection.roundId && collection.roundId !== liveRoundId ? (
                          <Badge tone="outline">{t("selections.fromRound")}</Badge>
                        ) : null}
                      </span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {t(
                          collectionRangeLabel(collection).key,
                          collectionRangeLabel(collection).params,
                        )}
                        {collectionLead(collection) ? ` · ${collectionLead(collection)}` : ""}
                      </span>
                    </button>
                    <TargetPicker collection={collection} />
                    <IconTip label={t("selections.remove")}>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="opacity-0 group-hover:opacity-100"
                        onClick={() => removeCollection(collection.id)}
                      >
                        <Trash2 />
                      </Button>
                    </IconTip>
                  </div>
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/**
 * Writes the originals for every selection that needs it.
 *
 * The same call is what compilation will make before it writes a page: a page's
 * `sources[]` has to point at a file that exists, so this cannot be skipped and
 * must not have a second implementation.
 */
function SaveOriginalsButton() {
  const { t } = useTranslation();
  const collections = useClipsStore((state) => state.collections);
  const remapSourceKeys = useClipsStore((state) => state.remapSourceKeys);
  const entries = useAcquireStore((state) => state.entries);
  const recordWrittenPaths = useAcquireStore((state) => state.recordWrittenPaths);
  const project = useWorkspaceStore((state) => state.current);

  const pending = collections.filter((collection) => isPreviewSource(collection.sourceKey));

  const save = async () => {
    if (!project || pending.length === 0) return;
    await runAsActivity({
      kind: "save",
      title: t("selections.saveTitle", { count: pending.length }),
      module: "sources",
      message: t("selections.saving"),
      doneMessage: () => t("selections.saved"),
      run: async (ctx) => {
        const captures = entries
          .map((entry) => entry.capture)
          .filter((capture): capture is NonNullable<typeof capture> => Boolean(capture));
        try {
          const result = await ensureSourcesOnDisk({
            project,
            collections: pending,
            captures,
          });
          if (ctx.signal.aborted) return result;
          await remapSourceKeys(result.remaps);
          recordWrittenPaths(result.writtenPaths);
          return result;
        } catch (err) {
          // These two are expected outcomes with something the user can do, so
          // they are worded here rather than shown as a raw failure.
          if (err instanceof MissingSourcesError) {
            throw new ShownError(
              i18n.t("selections.missingSources", {
                labels: missingSourceLabels(err),
              }),
            );
          }
          if (err instanceof SourceNotStoredError) {
            if (err.detail) ctx.note(err.detail);
            throw new ShownError(i18n.t("selections.storeFailed"));
          }
          throw err;
        }
      },
    }).catch(() => {
      // Already reported on the job; the list stays as it was.
    });
  };

  return (
    <IconTip
      label={
        pending.length === 0
          ? t("selections.allStored")
          : t("selections.saveOriginals", { count: pending.length })
      }
    >
      <Button
        variant="outline"
        size="sm"
        disabled={pending.length === 0}
        onClick={() => void save()}
      >
        {pending.length === 0 ? <Check /> : <HardDriveDownload />}
        {t("selections.saveShort")}
      </Button>
    </IconTip>
  );
}

/**
 * Which page a selection is meant for.
 *
 * A selection can say so, and saying so is what puts that page in front of the
 * planning pass — including a page no search proposed, and including material
 * outside any search. Left unset, the pass decides on its own.
 *
 * The suggestions are the pages already in play: the search's own list, and the
 * project's existing notes. A page that is not among them still works, which is the
 * point — typing it is how a reader says "this passage is for a page that does not
 * exist yet".
 */
function TargetPicker({ collection }: { collection: BlockCollection }) {
  const { t } = useTranslation();
  const setTarget = useClipsStore((state) => state.setTarget);
  const session = useDeepSearchStore((state) => state.session);
  const files = useDocsStore((state) => state.files);
  const [draft, setDraft] = useState("");

  const suggestions = useMemo(() => {
    const out: { slug: string; title: string; note?: string }[] = [];
    const seen = new Set<string>();
    for (const page of session?.whitelist ?? []) {
      if (!page.slug || seen.has(page.slug)) continue;
      seen.add(page.slug);
      out.push({ slug: page.slug, title: page.title || page.slug, note: "round" });
    }
    for (const file of files) {
      if (!isBindableWikiPage(file.path)) continue;
      const slug = wikiPathSlug(file.path);
      if (!slug || seen.has(slug)) continue;
      seen.add(slug);
      out.push({ slug, title: file.title || file.name, note: "wiki" });
    }
    return out;
  }, [session, files]);

  const bound = (collection.targetSlug ?? "").trim();
  const current = suggestions.find((item) => item.slug === bound);

  const choose = (slug: string) => {
    setTarget(collection.id, slug);
    setDraft("");
  };

  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            className={cn(
              "mt-0.5 shrink-0 rounded px-1 text-[10px]",
              bound
                ? "text-cite underline underline-offset-2"
                : "text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100",
            )}
            aria-label={t("selections.target.label")}
          >
            {bound ? current?.title ?? bound : t("selections.target.short")}
          </button>
        }
      />
      <PopoverContent className="w-64 p-2">
        <p className="text-[11px] font-medium">{t("selections.target.label")}</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          {t("selections.target.hint")}
        </p>
        <div className="mt-1.5 flex items-center gap-1">
          <Input
            className="h-7 text-[11px]"
            value={draft}
            placeholder={t("selections.target.placeholder")}
            aria-label={t("selections.target.placeholder")}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && draft.trim()) choose(draft.trim());
            }}
          />
          <Button size="sm" disabled={!draft.trim()} onClick={() => choose(draft.trim())}>
            {t("selections.target.set")}
          </Button>
        </div>
        {suggestions.length > 0 ? (
          <ul className="mt-1.5 max-h-40 overflow-y-auto scroll-thin">
            {suggestions.map((item) => (
              <li key={item.slug}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-baseline gap-1.5 rounded px-1 py-0.5 text-left text-[11px] hover:bg-accent/50",
                    item.slug === bound && "text-cite",
                  )}
                  onClick={() => choose(item.slug)}
                >
                  <span className="min-w-0 flex-1 truncate">{item.title}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {t(`selections.target.${item.note}`)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {bound ? (
          <Button
            variant="ghost"
            size="sm"
            className="mt-1.5 w-full"
            onClick={() => setTarget(collection.id, "")}
          >
            {t("selections.target.clear")}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}