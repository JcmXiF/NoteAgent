import { useEffect } from "react";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useWritingStore } from "@/stores/writing-store";

/** The project's writing state, read once for whichever writing panel asks. */
export function useWritingState() {
  const project = useWorkspaceStore((state) => state.current);
  const state = useWritingStore((s) => s.state);
  const error = useWritingStore((s) => s.error);
  const load = useWritingStore((s) => s.load);

  useEffect(() => {
    if (project) void load(project);
  }, [project, load]);

  return { project, state, error, load };
}
