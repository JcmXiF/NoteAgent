import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { TaskFrame, TaskWriteRequest } from "@/lib/api/types";
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
  const frames = useTaskStore((state) => state.frames);
  const approvalView = useTaskStore((state) => state.approvalView);
  const error = useTaskStore((state) => state.error);
  const picking = useTaskStore((state) => state.picking);
  const pickError = useTaskStore((state) => state.pickError);
  const pickNotice = useTaskStore((state) => state.pickNotice);
  const framing = useTaskStore((state) => state.framing);
  const frameError = useTaskStore((state) => state.frameError);
  const frameNotice = useTaskStore((state) => state.frameNotice);
  const deciding = useTaskStore((state) => state.deciding);
  const decisionError = useTaskStore((state) => state.decisionError);
  const decisionNotice = useTaskStore((state) => state.decisionNotice);
  const load = useTaskStore((state) => state.load);
  const pick = useTaskStore((state) => state.pick);
  const frame = useTaskStore((state) => state.frame);
  const decide = useTaskStore((state) => state.decide);
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
  const request = approvalView?.request ?? null;
  // The evidence a section names: the bounded previews the task already gave
  // for the material and the frame. The block text itself never leaves staging.
  const previews = new Map<string, string>();
  for (const material of staged) {
    for (const block of material.blocks ?? []) {
      previews.set(block.id, block.preview);
    }
  }
  for (const frame of frames?.frames ?? []) {
    for (const block of frame.blocks ?? []) {
      if (block.preview) previews.set(block.id, block.preview);
    }
  }
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
      {/* The go-ahead that stands, or the fact that none does. A frame change
          clears it, so this line is where the reader sees that it is gone. */}
      <p className="mt-0.5 text-[10px] text-muted-foreground">
        {summary.approval
          ? t("task.approved", {
              by: t(`task.source.${summary.approval.by}`, {
                defaultValue: summary.approval.by,
              }),
              rev: summary.approval.rev,
            })
          : t("task.noApproval")}
      </p>
      {request ? (
        <WriteRequestPanel
          request={request}
          rev={summary.rev}
          previews={previews}
          busy={deciding}
          onDecide={(decision) => void decide(decision)}
        />
      ) : null}
      {decisionNotice ? (
        <p role="status" className="mt-1 text-muted-foreground">
          {decisionNotice}
        </p>
      ) : null}
      {decisionError ? (
        <p role="alert" className="mt-1 text-destructive">
          {decisionError}
        </p>
      ) : null}
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
                <FrameEditor
                  materialId={material.id}
                  blocks={material.blocks ?? []}
                  frame={(frames?.frames ?? []).find(
                    (item) => item.docId === material.id,
                  )}
                  rev={frames?.rev ?? summary.rev}
                  busy={framing === material.id}
                  onSave={frame}
                />
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
      {frameNotice ? (
        <p role="status" className="mt-1 text-muted-foreground">
          {frameNotice}
        </p>
      ) : null}
      {frameError ? (
        <p role="alert" className="mt-1 text-destructive">
          {frameError}
        </p>
      ) : null}
      <FrameList frames={frames} />
    </div>
  );
}

/**
 * The task's frames, and who drew each one.
 *
 * A frame is the writing basis the agent reads; this is the reader's view of
 * it. The block previews are the bounded ones the core sends, never full text.
 */
function FrameList({
  frames,
}: {
  frames: ReturnType<typeof useTaskStore.getState>["frames"];
}) {
  const { t } = useTranslation();
  const shown = frames?.frames ?? [];
  return (
    <div className="mt-2 border-t pt-1.5">
      <p className="font-medium">{t("task.frames", { count: shown.length })}</p>
      {shown.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-1">
          {shown.map((frame) => (
            <li key={frame.id} className="min-w-0">
              <span className="block truncate text-foreground">
                {frame.docId}
              </span>
              <span className="block text-[10px] text-muted-foreground">
                {t("task.frameSource", {
                  source: t(`task.source.${frame.source}`, {
                    defaultValue: frame.source,
                  }),
                })}{" "}
                · {t("task.blocks", { count: frame.blockCount })}
              </span>
              {(frame.blocks ?? []).length > 0 ? (
                <ul className="mt-0.5 flex flex-col gap-px">
                  {(frame.blocks ?? []).slice(0, 6).map((block) => (
                    <li
                      key={block.id}
                      className="truncate text-[10px] text-muted-foreground"
                      title={block.preview}
                    >
                      <span className="text-foreground/70">[{block.id}]</span>{" "}
                      {block.preview}
                    </li>
                  ))}
                </ul>
              ) : null}
              {(frame.blocksOmitted ?? 0) > 0 ? (
                <span className="block text-[10px] text-muted-foreground">
                  {t("task.frameMore", { count: frame.blocksOmitted ?? 0 })}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-muted-foreground">{t("task.framesEmpty")}</p>
      )}
    </div>
  );
}

/**
 * The person's gate: the page, action, sections and evidence an agent proposed.
 *
 * It renders the pending request stored in the shared task, so this panel and
 * the chat card show the same proposal. Approve and reject go to the human-only
 * route; no agent tool can write either.
 */
function WriteRequestPanel({
  request,
  rev,
  previews,
  busy,
  onDecide,
}: {
  request: TaskWriteRequest;
  rev: number;
  previews: Map<string, string>;
  busy: "" | "approve" | "reject";
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const { t } = useTranslation();
  const spec = request.spec;
  return (
    <section
      aria-label={t("task.writeRequest")}
      className="mt-2 rounded-md border border-warning/50 bg-warning/5 p-1.5"
    >
      <div className="flex items-center gap-2">
        <p className="font-medium">{t("task.writeRequest")}</p>
        <span className="ml-auto text-[10px] text-muted-foreground">
          {t("task.rev", { rev })}
        </span>
      </div>
      <p className="mt-0.5 truncate text-foreground" title={spec.title}>
        {spec.title || spec.slug}
      </p>
      <p className="text-[10px] text-muted-foreground">
        {spec.slug}
        {" · "}
        {t(`task.action.${spec.action}`, { defaultValue: spec.action })}
      </p>
      <ol className="mt-1 flex flex-col gap-1">
        {spec.sections.map((section, index) => (
          <li key={`${index}:${section.title}`} className="min-w-0">
            <p className="truncate text-foreground" title={section.title}>
              {section.title}
            </p>
            <ul className="mt-0.5 flex flex-col gap-px">
              {section.blocks.map((block) => (
                <li
                  key={block}
                  className="truncate text-[10px] text-muted-foreground"
                  title={previews.get(block) ?? block}
                >
                  <span className="text-foreground/70">[{block}]</span>{" "}
                  {previews.get(block) ?? ""}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <div className="mt-1 flex gap-1">
        <Button
          size="sm"
          className="h-5 px-1.5"
          disabled={busy !== ""}
          onClick={() => onDecide("approve")}
        >
          {busy === "approve" ? t("task.approving") : t("task.approve")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-5 px-1.5"
          disabled={busy !== ""}
          onClick={() => onDecide("reject")}
        >
          {busy === "reject" ? t("task.rejecting") : t("task.reject")}
        </Button>
      </div>
    </section>
  );
}

/**
 * Tick the blocks one material contributes.
 *
 * The current frame starts ticked, so adding or removing a block is one tick
 * and one save. A frame may name blocks past this view's bounded index; those
 * stay ticked and are saved back, and the count says how many they are.
 */
function FrameEditor({
  materialId,
  blocks,
  frame,
  rev,
  busy,
  onSave,
}: {
  materialId: string;
  blocks: { id: string; preview?: string }[];
  frame?: TaskFrame;
  rev: number;
  busy: boolean;
  onSave: (docId: string, blocks: string[], expectedRev: number) => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => {
    setPicked((frame?.blocks ?? []).map((block) => block.id));
  }, [frame, materialId]);
  if (blocks.length === 0) return null;
  const inView = new Set(blocks.map((block) => block.id));
  const beyond = picked.filter((id) => !inView.has(id));
  const toggle = (id: string) =>
    setPicked((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  return (
    <div className="mt-1 border-t border-dashed pt-1">
      <p className="text-[10px] text-muted-foreground">
        {t("task.frameHint")}
      </p>
      <ul className="mt-0.5 flex flex-col gap-px">
        {blocks.map((block) => (
          <li key={block.id}>
            <label className="flex items-start gap-1 text-[10px] text-muted-foreground">
              <input
                type="checkbox"
                className="mt-0.5 size-3 shrink-0"
                checked={picked.includes(block.id)}
                onChange={() => toggle(block.id)}
              />
              <span className="min-w-0 flex-1">
                <span className="text-foreground/70">[{block.id}]</span>{" "}
                {block.preview ?? ""}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {beyond.length > 0 ? (
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          {t("task.frameMore", { count: beyond.length })}
        </p>
      ) : null}
      <Button
        variant="ghost"
        size="sm"
        className="mt-1 h-5 px-1.5"
        disabled={busy || picked.length === 0}
        onClick={() => onSave(materialId, picked, rev)}
      >
        {busy ? t("task.framing") : t("task.frameSave")}
      </Button>
    </div>
  );
}
