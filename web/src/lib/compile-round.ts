import type {
  CompletedRoundPayload,
  DoctorFinding,
  IngestDone,
} from "@/lib/api/types";

/**
 * What a compile round produced, however the view came by it.
 *
 * The panel reads this rather than the raw response, because the round on screen
 * is either the one that just finished or the one the project kept from last
 * time — and both have to be told the same way, or the view would describe one of
 * them differently from the other.
 */
export type CompileRound = {
  /** What the pages were written for, as the planning pass was given it. */
  intent: string;
  written: { path: string; mode: string }[];
  findings: DoctorFinding[];
  /** The check stopped listing before it ran out of findings. */
  truncated: boolean;
  /** Files the round could not write, as the response named them. */
  failures: { path: string; detail: string }[];
  /** Whether this came from the project rather than from the run just made. */
  fromProject: boolean;
};

/** The round the response just reported. */
export function roundFromIngestDone(
  payload: IngestDone,
  intent: string,
): CompileRound {
  return {
    intent,
    written: payload.results.flatMap((item) =>
      item.written.map((file) => ({ path: file.path, mode: file.mode })),
    ),
    findings: payload.results.flatMap((item) => item.doctor?.findings ?? []),
    truncated: payload.results.some((item) => item.doctor?.truncated ?? false),
    failures: payload.errors,
    fromProject: false,
  };
}

/** The round the project kept. `null` when it has none. */
export function roundFromRecord(
  record: CompletedRoundPayload | null,
): CompileRound | null {
  if (!record) return null;
  return {
    intent: record.intent,
    written: record.written.map((file) => ({ path: file.path, mode: file.mode })),
    findings: record.doctor?.findings ?? [],
    truncated: record.doctor?.truncated ?? false,
    // The check's findings are the round's report. A refused write is reported
    // where it happened, so nothing here invents one from the record.
    failures: [],
    fromProject: true,
  };
}
