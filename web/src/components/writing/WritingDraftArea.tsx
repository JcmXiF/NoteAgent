import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  EmptyLine,
} from "@/components/writing/WritingParts";
import type { WritingDraftPage } from "@/lib/api/writing";
import {
  isMissingReason,
  staleMessage,
} from "@/lib/writing";

export function Draft({
  pages,
  empty,
  onSection,
  onRemoveSection,
}: {
  pages: WritingDraftPage[];
  empty: string;
  onSection: (
    page: number,
    section: number,
    field: "heading" | "body",
    value: string,
  ) => void;
  onRemoveSection: (page: number, section: number) => void;
}) {
  const { t } = useTranslation();
  const total = pages.reduce(
    (sum, page) => sum + (page.sections?.length ?? 0),
    0,
  );

  return (
    <Card
      title={t("writing.areas.draft")}
      count={total}
      className="md:col-span-2"
    >
      {pages.length === 0 ? (
        <EmptyLine>{empty}</EmptyLine>
      ) : (
        <div className="mt-2 space-y-4">
          {pages.map((page, pageIndex) => (
            <div key={page.wikiFile ?? pageIndex}>
              <p className="text-xs font-medium">
                {page.title ?? page.wikiFile ?? ""}
              </p>
              <p className="text-[11px] text-muted-foreground">
                {t("writing.draft.pageMeta", {
                  version: page.version ?? 0,
                  status: t(
                    `writing.draft.status.${page.status ?? "draft"}`,
                  ),
                })}
              </p>
              {(page.effectIds ?? []).length > 0 ? (
                <p className="mt-1 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                  {page.effectIds?.map((id) => (
                    <code key={id}>{id}</code>
                  ))}
                </p>
              ) : null}
              <ul className="mt-2 space-y-2">
                {(page.sections ?? []).map((section, sectionIndex) => (
                  <li
                    key={section.id ?? sectionIndex}
                    className="rounded-md border bg-background/60 p-2"
                  >
                    <div className="flex items-center gap-1">
                      <input
                        className="min-w-0 flex-1 bg-transparent text-xs font-medium outline-none"
                        value={section.heading ?? ""}
                        placeholder={t("writing.edit.sectionHeading")}
                        aria-label={t("writing.edit.sectionHeading")}
                        onChange={(event) =>
                          onSection(
                            pageIndex,
                            sectionIndex,
                            "heading",
                            event.target.value,
                          )
                        }
                      />
                      {section.humanEdited ? (
                        <Badge tone="warning">
                          {t("writing.edited")}
                        </Badge>
                      ) : null}
                      {section.stale ? (
                        <Badge
                          tone={
                            isMissingReason(section.staleReason)
                              ? "destructive"
                              : "warning"
                          }
                        >
                          {t(
                            isMissingReason(section.staleReason)
                              ? "writing.stale.missingBadge"
                              : "writing.stale.badge",
                          )}
                        </Badge>
                      ) : null}
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={t("writing.edit.remove")}
                        onClick={() =>
                          onRemoveSection(pageIndex, sectionIndex)
                        }
                      >
                        <X />
                      </Button>
                    </div>
                    <textarea
                      className="mt-1 w-full resize-y bg-transparent text-[11px] text-muted-foreground outline-none"
                      rows={3}
                      value={section.body ?? ""}
                      placeholder={t("writing.edit.sectionBody")}
                      aria-label={t("writing.edit.sectionBody")}
                      onChange={(event) =>
                        onSection(
                          pageIndex,
                          sectionIndex,
                          "body",
                          event.target.value,
                        )
                      }
                    />
                    {section.stale ? (
                      <p
                        className={`mt-1 text-[10px] ${
                          isMissingReason(section.staleReason)
                            ? "text-destructive"
                            : "text-warning"
                        }`}
                      >
                        {staleMessage(
                          section.staleReason,
                          section.heading,
                        )}
                      </p>
                    ) : null}
                    {(section.citations ?? []).length > 0 ? (
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {(section.citations ?? [])
                          .map((citation) => citation.frameId)
                          .filter(Boolean)
                          .join(", ")}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
