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
};

export type PipelineStartInput = {
  intent: string;
  startedBy?: PipelineStartedBy;
  sessionId?: string;
  queryMode?: PipelineQueryMode;
  materialMode?: PipelineMaterialMode;
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

/** Starts one run; the project refuses a second while the first is active. */
export function startPipelineRun(input: PipelineStartInput): Promise<PipelineRun> {
  return postJson<PipelineRun>("/v1/pipeline/runs", input);
}

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

export function cancelPipelineRun(id: string): Promise<PipelineRun> {
  return postJson<PipelineRun>(
    `/v1/pipeline/runs/${encodeURIComponent(id)}/cancel`,
    {},
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
