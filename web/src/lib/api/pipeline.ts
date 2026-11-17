import { getJson, postJson } from "@/lib/api/client";

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
};

export type PipelineStartInput = {
  intent: string;
  startedBy?: PipelineStartedBy;
  sessionId?: string;
  queryMode?: PipelineQueryMode;
  materialMode?: PipelineMaterialMode;
};

/** Starts one run; the project refuses a second while the first is active. */
export function startPipelineRun(input: PipelineStartInput): Promise<PipelineRun> {
  return postJson<PipelineRun>("/pipeline/runs", input);
}

export function fetchPipelineRuns(): Promise<{ runs: PipelineRun[] }> {
  return getJson<{ runs: PipelineRun[] }>("/pipeline/runs");
}

export function cancelPipelineRun(id: string): Promise<PipelineRun> {
  return postJson<PipelineRun>(
    `/pipeline/runs/${encodeURIComponent(id)}/cancel`,
    {},
  );
}
