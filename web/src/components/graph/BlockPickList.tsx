import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { blockPreview, groupBlocksByFile } from "@/lib/deep-search";
import { titleOfPath } from "@/lib/project-tree";
import type { DeepSearchBlock, TreeFile } from "@/lib/api/types";

/**
 * Passages a round suggests, grouped by the file they came from.
 *
 * Grouped because a reader judges a passage against the ones around it: two
 * passages from one page separated by one from another read as a jumble. The group
 * header carries the count that has been ticked, so a reader can see how much of
 * one file they are taking without counting rows.
 *
 * The text is a button and the tick is not, because they are different actions:
 * the tick says "take this one", the text says "show me where it is".
 */
export function BlockPickList({
  blocks,
  files,
  onToggle,
  onOpen,
}: {
  blocks: DeepSearchBlock[];
  files: TreeFile[];
  onToggle: (locator: string, accepted: boolean) => void;
  onOpen: (locator: string) => void;
}) {
  const { t } = useTranslation();
  const groups = groupBlocksByFile(blocks);
  return (
    <ul className="flex flex-col pb-1">
      {groups.map((group) => (
        <li key={group.path || "none"}>
          <div className="flex items-baseline gap-1.5 px-2 pt-2 pb-0.5">
            <span className="min-w-0 truncate text-[11px] font-medium">
              {group.path ? titleOfPath(group.path, files) : t("deepSearch.blocks.noFile")}
            </span>
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
              {t("deepSearch.blocks.taken", {
                taken: group.items.filter((item) => item.accepted).length,
                total: group.items.length,
              })}
            </span>
          </div>
          <ul>
            {group.items.map((block) => (
              <li key={block.locator}>
                <div className="flex items-start gap-1.5 px-2 py-1 hover:bg-accent/40">
                  <Checkbox
                    className="mt-0.5"
                    checked={block.accepted}
                    onCheckedChange={(checked) => onToggle(block.locator, checked)}
                    aria-label={t("deepSearch.blocks.include")}
                  />
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left text-[11px] leading-snug"
                    onClick={() => onOpen(block.locator)}
                  >
                    {blockPreview(block) || (
                      // A figure or a table cell has no text of its own; a blank row
                      // would look like something failed to load.
                      <span className="text-muted-foreground italic">
                        {t("deepSearch.blocks.noText")}
                      </span>
                    )}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
