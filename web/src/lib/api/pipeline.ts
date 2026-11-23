import { getJson, postJson } from "@/lib/api/client";
import type { DerivedBlock } from "@/lib/api/types";

/** How far a run has got, as the core names it. */
export type PipelineRunStatus =
  | "running"
  | "awaiting_confirmation"
  | "compiling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export type PipelineQueryMode = "precise" | "model";
export type PipelineMaterialMode = "manual" | "auto";
export type PipelineStartedBy = "human" | "session";

/** What the compile wrote, skipped and failed, and the effects it left. */
export type PipelineRunResult = {
  planRevision?: number;
  written?: string[];
  skipped?: { path: string; reason: string }[];
  failed?: { path: string; reason: string }[];
  effectIds?: string[];
};

/** One run of the active project's pipeline. */
export type PipelineRun = {
  id: string;
  intent: string;
  startedBy: PipelineStartedBy;
  sessionId: string;
  queryMode: PipelineQueryMode;
  materialMode: PipelineMaterialMode;
  status: PipelineRunStatus;
  stage: string;
  startedAt: string;
  updatedAt: string;
  planRevision: number;
  error: string;
  /** What the stages have done so far: acquired, skipped and why. */
  detail: Record<string, unknown>;
  /** What the compile did, once a person approved it. */
  result?: PipelineRunResult;
};

export type PipelineRestoreClassification =
  | "unchanged"
  | "changed_by_person"
  | "changed_by_run"
  | "changed_by_restore"
  | "unknown_external"
  | "missing"
  | "renamed";

export type PipelineRestorePage = {
  path: string;
  kind: string;
  runId: string;
  at: string;
  classification: PipelineRestoreClassification;
  defaultAction: "restore" | "skip";
  effectId: string;
  before: string;
  after: string;
  currentPath: string;
};

export type PipelineRestorePreview = {
  runId: string;
  pages: PipelineRestorePage[];
};

export type PipelineRestoreChoice = {
  path: string;
  action: "restore" | "skip";
  force?: boolean;
};

export type PipelineRestoreResult = {
  runId: string;
  restored: {
    path: string;
    classification: string;
    effectId: string;
    before: string;
    after: string;
  }[];
  skipped: { path: string; reason: string }[];
  conflicted: {
    path: string;
    classification: string;
    currentPath?: string;
    error?: string;
  }[];
  failed: { path: string; error: string }[];
};

export type PipelineMaterialState =
  | "staged"
  | "framed"
  | "planned"
  | "promoted"
  | "compiled"
  | "changed"
  | "dropped";

/** One original this run collected, and where it stands. */
export type PipelineMaterial = {
  material: string;
  state: PipelineMaterialState;
  runId: string;
  updatedAt: string;
  title: string;
  kind: string;
  fidelity: string;
};

export function fetchPipelineRuns(): Promise<{ runs: PipelineRun[] }> {
  return getJson<{ runs: PipelineRun[] }>("/v1/pipeline/runs");
}

export function fetchPipelineMaterials(
  runId: string,
): Promise<{ materials: PipelineMaterial[] }> {
  return getJson<{ materials: PipelineMaterial[] }>(
    `/v1/pipeline/runs/${encodeURIComponent(runId)}/materials`,
  );
}



export function fetchPipelineRestorePreview(
  runId: string,
): Promise<PipelineRestorePreview> {
  return getJson<PipelineRestorePreview>(
    `/v1/pipeline/runs/${encodeURIComponent(runId)}/restore/preview`,
  );
}

export function restorePipelineRun(
  runId: string,
  pages: PipelineRestoreChoice[],
): Promise<PipelineRestoreResult> {
  return postJson<PipelineRestoreResult>(
    `/v1/pipeline/runs/${encodeURIComponent(runId)}/restore`,
    { runId, pages },
  );
}

/** The staged blocks behind one material, for the source view. */
export function fetchPipelineMaterialBlocks(
  runId: string,
  material: string,
): Promise<{ material: string; blocks: DerivedBlock[] }> {
  const query = new URLSearchParams({ material });
  return getJson(
    `/v1/pipeline/runs/${encodeURIComponent(runId)}/materials/blocks?${query}`,
  );
}
