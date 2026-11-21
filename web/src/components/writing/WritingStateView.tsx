import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Archive,
  Loader2,
  PenLine,
  RefreshCw,
  Save,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Draft } from "@/components/writing/WritingDraftArea";
import {
  Frames,
  Searches,
  SelectedPages,
} from "@/components/writing/WritingStateAreas";
import { Card } from "@/components/writing/WritingParts";
import { useWritingState } from "@/components/writing/use-writing-state";
import { writingAttention, type WritingAttention } from "@/lib/writing";
import {
  removeFrame,
  removePage,
  removeSearchHit,
  removeSection,
  updateFrameBlocks,
  updatePageTitle,
  updateSearchHit,
  updateSection,
} from "@/lib/writing-edits";
import type { PaneSlot } from "@/stores/layout-store";
import { editableFrom } from "@/stores/writing-store";

/**
 * The project's writing state, editable in the four areas the agent works on.
 *
 * Edits stay local until Save: the save sends the whole state's base version
 * and the four areas, and a run's lease or a newer state is answered with a
 * refusal that leaves the local edits in place. The archive list lets a person
 * put the current topic away and bring an older one back.
 */
export function WritingStateView(_props: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const {
    project,
    state,
    draft,
    dirty,
    notice,
    saving,
    archives,
    error,
    loading,
    load,
    edit,
    save,
    reload,
    archive,
    activate,
  } = useWritingState();

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
        {loading ? <Loader2 className="size-4 animate-spin" /> : null}
        {loading ? t("writing.loading") : t("writing.empty")}
      </div>
    );
  }

  const editable = draft ?? editableFrom(state);
  const writer = state.writer
    ? state.writer.id || state.writer.kind || t("writing.noWriter")
    : t("writing.noWriter");
  const attention: WritingAttention[] = writingAttention(state);

  const handleHit = (
    searchIndex: number,
    hitIndex: number,
    field: "title" | "url" | "snippet",
    value: string,
  ) =>
    edit((current) =>
      updateSearchHit(current, searchIndex, hitIndex, field, value),
    );
  const handleRemoveHit = (searchIndex: number, hitIndex: number) =>
    edit((current) => removeSearchHit(current, searchIndex, hitIndex));
  const handlePageTitle = (index: number, value: string) =>
    edit((current) => updatePageTitle(current, index, value));
  const handleRemovePage = (index: number) =>
    edit((current) => removePage(current, index));
  const handleFrameBlocks = (index: number, value: string) =>
    edit((current) => updateFrameBlocks(current, index, value));
  const handleRemoveFrame = (index: number) =>
    edit((current) => removeFrame(current, index));
  const handleSection = (
    pageIndex: number,
    sectionIndex: number,
    field: "heading" | "body",
    value: string,
  ) =>
    edit((current) =>
      updateSection(
        current,
        pageIndex,
        sectionIndex,
        field,
        value,
      ),
    );
  const handleRemoveSection = (
    pageIndex: number,
    sectionIndex: number,
  ) =>
    edit((current) =>
      removeSection(current, pageIndex, sectionIndex),
    );

  const discardOr = (act: () => void) => {
    if (dirty && !window.confirm(t("writing.discardUnsaved"))) {
      return;
    }
    act();
  };

  return (
    <article className="flex h-full min-h-0 flex-col overflow-auto">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-3">
        <PenLine className="size-4 text-muted-foreground" />
        <h1 className="text-sm font-medium">{t("writing.title")}</h1>
        <span className="text-[11px] text-muted-foreground">
          {t("writing.version", { version: state.stateVersion })}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("writing.writer", { writer })}
        </span>
        {dirty ? (
          <Badge tone="warning">{t("writing.dirty")}</Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={!dirty || saving}
          >
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            {saving ? t("writing.saving") : t("writing.save")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => discardOr(() => void archive())}
            disabled={saving}
          >
            <Archive /> {t("writing.archive")}
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => void load(project, true)}
            aria-label={t("writing.retry")}
          >
            <RefreshCw />
          </Button>
        </div>
      </header>
      {notice ? (
        <div className="flex shrink-0 items-center gap-2 border-b bg-warning/10 px-4 py-2 text-[11px]">
          <span className="flex-1">{t(`writing.notice.${notice}`)}</span>
          {notice !== "busy" ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => discardOr(() => void reload())}
            >
              {t("writing.reload")}
            </Button>
          ) : null}
        </div>
      ) : null}
      <details className="shrink-0 border-b px-4 py-2 text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          {t("writing.archives")} ({archives.length})
        </summary>
        {archives.length === 0 ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {t("writing.archivesEmpty")}
          </p>
        ) : (
          <ul className="mt-2 space-y-1">
            {archives.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center gap-2"
              >
                <span className="text-[11px] text-muted-foreground">
                  {t("writing.archiveSummary", {
                    version: item.stateVersion ?? 0,
                    searches: item.searches ?? 0,
                    pages: item.selectedPages ?? 0,
                    frames: item.frames ?? 0,
                    draft: item.draftPages ?? 0,
                  })}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={saving}
                  onClick={() =>
                    discardOr(() => void activate(item.id ?? ""))
                  }
                >
                  {t("writing.activate")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </details>
      <div className="grid gap-3 p-4 md:grid-cols-2">
        <Searches
          searches={editable.searches}
          empty={t("writing.empty")}
          onHit={handleHit}
          onRemoveHit={handleRemoveHit}
        />
        <SelectedPages
          pages={editable.selectedPages}
          empty={t("writing.empty")}
          onTitle={handlePageTitle}
          onRemove={handleRemovePage}
        />
        <Frames
          frames={editable.frames}
          pages={editable.selectedPages}
          empty={t("writing.empty")}
          onBlocks={handleFrameBlocks}
          onRemove={handleRemoveFrame}
        />
        <Draft
          pages={editable.draft.pages}
          empty={t("writing.empty")}
          onSection={handleSection}
          onRemoveSection={handleRemoveSection}
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
