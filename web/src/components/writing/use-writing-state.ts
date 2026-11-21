import { useEffect } from "react";
import { isSseAborted } from "@/lib/api/client";
import { streamWritingState } from "@/lib/api/writing";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useWritingStore } from "@/stores/writing-store";

/** The project's writing state, read for whichever writing panel asks. */
export function useWritingState() {
  const project = useWorkspaceStore((state) => state.current);
  const state = useWritingStore((s) => s.state);
  const draft = useWritingStore((s) => s.draft);
  const dirty = useWritingStore((s) => s.dirty);
  const notice = useWritingStore((s) => s.notice);
  const saving = useWritingStore((s) => s.saving);
  const archives = useWritingStore((s) => s.archives);
  const error = useWritingStore((s) => s.error);
  const loading = useWritingStore((s) => s.loading);
  const load = useWritingStore((s) => s.load);
  const edit = useWritingStore((s) => s.edit);
  const save = useWritingStore((s) => s.save);
  const reload = useWritingStore((s) => s.reload);
  const archive = useWritingStore((s) => s.archive);
  const activate = useWritingStore((s) => s.activate);

  useEffect(() => {
    if (project) void load(project);
  }, [project, load]);

  useEffect(() => {
    if (!project) return;
    const controller = new AbortController();
    // A run's tool or another tab moved the state: read it again. The store
    // keeps a local draft and its base version, so this never silently
    // replaces edits or lets a save overwrite the change the reader missed.
    void streamWritingState(
      project,
      () => {
        void useWritingStore.getState().load(project, true);
      },
      controller.signal,
    ).catch((err) => {
      if (isSseAborted(err, controller.signal)) return;
      // A stream that ended on its own leaves the last read in place; the
      // refresh button and the next open read again.
    });
    return () => controller.abort();
  }, [project]);

  return {
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
  };
}
