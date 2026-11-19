import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useTaskStore } from "@/stores/task-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * What the project's writing task currently holds.
 *
 * The agent reads the same bounded views through `inspect`; this panel is the
 * reader's door into that state. It shows the revision because a stale view is
 * the thing a conflict is about.
 */
export function TaskView() {
  const { t } = useTranslation();
  const status = useTaskStore((state) => state.status);
  const summary = useTaskStore((state) => state.summary);
  const cards = useTaskStore((state) => state.cards);
  const materials = useTaskStore((state) => state.materials);
  const error = useTaskStore((state) => state.error);
  const picking = useTaskStore((state) => state.picking);
  const pickError = useTaskStore((state) => state.pickError);
  const pickNotice = useTaskStore((state) => state.pickNotice);
  const load = useTaskStore((state) => state.load);
  const pick = useTaskStore((state) => state.pick);
  const reset = useTaskStore((state) => state.reset);
  const project = useWorkspaceStore((state) => state.current);

  useEffect(() => {
    if (project) void load();
    else reset();
  }, [project, load, reset]);

  if (!project) return null;
  if (status === "loading" && summary === null) {
    return (
      <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
        {t("task.loading")}
      </p>
    );
  }
  if (error) {
    return (
      <div className="flex items-start gap-1.5 px-2 py-1.5">
        <p className="min-w-0 flex-1 text-[11px] text-destructive">{error}</p>
        <Button
          variant="ghost"
          size="sm"
          className="h-5 shrink-0 px-1.5"
          onClick={() => void load()}
        >
          {t("task.retry")}
        </Button>
      </div>
    );
  }
  if (summary === null) return null;

  const shown = cards?.cards ?? [];
  const staged = materials?.materials ?? [];
  return (
    <div className="px-2 py-1.5 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="font-medium">
          {t(`task.status.${summary.status}`, {
            defaultValue: summary.status,
          })}
        </span>
        <span className="text-muted-foreground">
          {t("task.rev", { rev: summary.rev })}
        </span>
        <span className="ml-auto text-muted-foreground">
          {t("task.cards", { count: summary.counts.cards })}
        </span>
      </div>
      {shown.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-0.5">
          {shown.map((card) => (
            <li key={card.id} className="flex min-w-0 items-start gap-1">
              <span className="min-w-0 flex-1">
                <span
                  className="block truncate text-foreground"
                  title={card.title || card.url}
                >
                  {card.title || card.url}
                </span>
                {card.site ? (
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {card.site}
                  </span>
                ) : null}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 shrink-0 px-1.5"
                disabled={picking !== ""}
                onClick={() => void pick(card.id, cards?.rev ?? summary.rev)}
              >
                {picking === card.id ? t("task.picking") : t("task.pick")}
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-muted-foreground">{t("task.empty")}</p>
      )}
      {pickNotice ? (
        <p role="status" className="mt-1 text-muted-foreground">
          {pickNotice}
        </p>
      ) : null}
      {pickError ? (
        <p role="alert" className="mt-1 text-destructive">
          {pickError}
        </p>
      ) : null}
      {staged.length > 0 ? (
        <div className="mt-2 border-t pt-1.5">
          <p className="font-medium">
            {t("task.materials", { count: staged.length })}
          </p>
          <ul className="mt-1 flex flex-col gap-1.5">
            {staged.map((material) => (
              <li key={material.id} className="min-w-0">
                <span className="block truncate text-foreground">
                  {material.title || material.id}
                </span>
                <span className="block truncate text-[10px] text-muted-foreground">
                  {material.id}
                </span>
                <span className="block text-[10px] text-muted-foreground">
                  {material.state} · {material.fidelity} ·{" "}
                  {t("task.blocks", { count: material.blockCount })}
                </span>
                {(material.blocks ?? []).length > 0 ? (
                  <ul className="mt-0.5 flex flex-col gap-px">
                    {(material.blocks ?? []).slice(0, 6).map((block) => (
                      <li
                        key={block.id}
                        className="truncate text-[10px] text-muted-foreground"
                        title={block.preview}
                      >
                        <span className="text-foreground/70">
                          [{block.id}]
                        </span>{" "}
                        {block.preview}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {(material.blocksOmitted ?? 0) > 0 ? (
                  <span className="block text-[10px] text-muted-foreground">
                    {t("task.moreBlocks", {
                      count: material.blocksOmitted ?? 0,
                    })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
