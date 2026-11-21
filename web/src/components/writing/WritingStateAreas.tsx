import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  EmptyLine,
} from "@/components/writing/WritingParts";
import type {
  WritingFrame,
  WritingSearch,
  WritingSelectedPage,
} from "@/lib/api/writing";

export function Searches({
  searches,
  empty,
  onHit,
  onRemoveHit,
}: {
  searches: WritingSearch[];
  empty: string;
  onHit: (
    search: number,
    hit: number,
    field: "title" | "url" | "snippet",
    value: string,
  ) => void;
  onRemoveHit: (search: number, hit: number) => void;
}) {
  const { t } = useTranslation();

  return (
    <Card title={t("writing.areas.searches")} count={searches.length}>
      {searches.length === 0 ? (
        <EmptyLine>{empty}</EmptyLine>
      ) : (
        <div className="mt-2 space-y-3">
          {searches.map((search, searchIndex) => (
            <div key={search.id ?? searchIndex}>
              <p className="text-[11px] text-muted-foreground">
                {t("writing.search.query", {
                  query: search.query ?? "",
                })}
              </p>
              {search.status === "failed" ? (
                <p className="text-xs text-destructive">
                  {t("writing.search.failed")}
                  {search.error ? ` · ${search.error}` : ""}
                </p>
              ) : (search.hits ?? []).length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("writing.search.noHits")}
                </p>
              ) : (
                <ul className="mt-1 space-y-2">
                  {(search.hits ?? []).map((hit, hitIndex) => (
                    <li
                      key={hit.id ?? hitIndex}
                      className="space-y-1 rounded-md border bg-background/60 p-2"
                    >
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] text-muted-foreground">
                          #{hit.rank ?? hitIndex + 1}
                        </span>
                        <input
                          className="min-w-0 flex-1 bg-transparent text-xs font-medium outline-none"
                          value={hit.title ?? ""}
                          placeholder={t("writing.edit.hitTitle")}
                          aria-label={t("writing.edit.hitTitle")}
                          onChange={(event) =>
                            onHit(
                              searchIndex,
                              hitIndex,
                              "title",
                              event.target.value,
                            )
                          }
                        />
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("writing.edit.remove")}
                          onClick={() =>
                            onRemoveHit(searchIndex, hitIndex)
                          }
                        >
                          <X />
                        </Button>
                      </div>
                      <input
                        className="w-full bg-transparent text-[10px] text-muted-foreground outline-none"
                        value={hit.url ?? ""}
                        placeholder={t("writing.edit.hitUrl")}
                        aria-label={t("writing.edit.hitUrl")}
                        onChange={(event) =>
                          onHit(
                            searchIndex,
                            hitIndex,
                            "url",
                            event.target.value,
                          )
                        }
                      />
                      <input
                        className="w-full bg-transparent text-[11px] text-muted-foreground outline-none"
                        value={hit.snippet ?? ""}
                        placeholder={t("writing.edit.hitSnippet")}
                        aria-label={t("writing.edit.hitSnippet")}
                        onChange={(event) =>
                          onHit(
                            searchIndex,
                            hitIndex,
                            "snippet",
                            event.target.value,
                          )
                        }
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function SelectedPages({
  pages,
  empty,
  onTitle,
  onRemove,
}: {
  pages: WritingSelectedPage[];
  empty: string;
  onTitle: (index: number, value: string) => void;
  onRemove: (index: number) => void;
}) {
  const { t } = useTranslation();

  return (
    <Card title={t("writing.areas.selectedPages")} count={pages.length}>
      {pages.length === 0 ? (
        <EmptyLine>{empty}</EmptyLine>
      ) : (
        <ul className="mt-2 space-y-2">
          {pages.map((page, index) => (
            <li
              key={page.id ?? index}
              className="rounded-md border bg-background/60 p-2"
            >
              <div className="flex items-center gap-1">
                <input
                  className="min-w-0 flex-1 bg-transparent text-xs font-medium outline-none"
                  value={page.title ?? ""}
                  placeholder={t("writing.edit.pageTitle")}
                  aria-label={t("writing.edit.pageTitle")}
                  onChange={(event) =>
                    onTitle(index, event.target.value)
                  }
                />
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("writing.edit.remove")}
                  onClick={() => onRemove(index)}
                >
                  <X />
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                {t(`writing.pages.${page.status ?? "fetching"}`)}
                {" · "}
                {t("writing.pages.blockCount", {
                  count: page.blockCount ?? 0,
                })}
              </p>
              {page.status === "failed" && page.error ? (
                <p className="text-[11px] text-destructive">
                  {page.error}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function Frames({
  frames,
  pages,
  empty,
  onBlocks,
  onRemove,
}: {
  frames: WritingFrame[];
  pages: WritingSelectedPage[];
  empty: string;
  onBlocks: (index: number, value: string) => void;
  onRemove: (index: number) => void;
}) {
  const { t } = useTranslation();
  const groups = new Map<
    string,
    { frame: WritingFrame; index: number }[]
  >();
  frames.forEach((frame, index) => {
    const key = frame.pageId ?? "";
    const list = groups.get(key) ?? [];
    list.push({ frame, index });
    groups.set(key, list);
  });
  const titleFor = (pageId: string) =>
    pages.find((page) => page.id === pageId)?.title ?? pageId;

  return (
    <Card title={t("writing.areas.frames")} count={frames.length}>
      {frames.length === 0 ? (
        <EmptyLine>{empty}</EmptyLine>
      ) : (
        <ul className="mt-2 space-y-2">
          {[...groups].map(([pageId, items]) => (
            <li key={pageId}>
              <p className="text-xs font-medium">{titleFor(pageId)}</p>
              <ul className="mt-1 space-y-1">
                {items.map(({ frame, index }) => (
                  <li
                    key={frame.id ?? index}
                    className="flex items-center gap-1 text-[11px] text-muted-foreground"
                  >
                    <code className="shrink-0 text-[10px]">
                      {frame.id}
                    </code>
                    <input
                      className="min-w-0 flex-1 bg-transparent text-[11px] outline-none"
                      value={(frame.blockIds ?? []).join(", ")}
                      placeholder={t("writing.edit.frameBlocks")}
                      aria-label={t("writing.edit.frameBlocks")}
                      onChange={(event) =>
                        onBlocks(index, event.target.value)
                      }
                    />
                    <span className="shrink-0 text-[10px]">
                      {t("writing.frames.blockCount", {
                        count: frame.blockIds?.length ?? 0,
                      })}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("writing.edit.remove")}
                      onClick={() => onRemove(index)}
                    >
                      <X />
                    </Button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
