import { describe, expect, it } from "vitest";
import { roundFromIngestDone, roundFromRecord } from "@/lib/compile-round";
import type { CompletedRoundPayload, DoctorPayload, IngestDone } from "@/lib/api/types";

/** A check that came back clean, as the core reports one. */
const clean = (): DoctorPayload => ({
  contentPages: 1,
  indexLinks: 2,
  errors: 0,
  warnings: 0,
  findingCount: 0,
  truncated: false,
  findings: [],
});

/** A round the response has just reported, with one page written. */
const done: IngestDone = {
  results: [
    {
      sourceIdentity: "clip:one",
      sourceSummaryPath: "sources/one.md",
      cacheHit: false,
      analysisChars: 10,
      generationChars: 20,
      parsedBlocks: 1,
      dryRun: false,
      written: [{ path: "wiki/a.md", mode: "create", bytes: 12 }],
      writeWarnings: [],
      parseWarnings: [],
      indexAdded: 1,
      indexSkipped: 0,
      doctor: clean(),
    },
  ],
  errors: [],
};

/** The same round as the project kept it. */
const record: CompletedRoundPayload = {
  intent: "Entropy",
  finishedAt: "2026-10-14T12:00:00+00:00",
  written: [{ path: "wiki/a.md", mode: "create" }],
  doctor: clean(),
  writeWarnings: [],
  parseWarnings: [],
};

describe("reading a round out of a response", () => {
  it("lists the files every page wrote, in the order they were reported", () => {
    const two = {
      ...done,
      results: [
        done.results[0]!,
        {
          ...done.results[0]!,
          written: [{ path: "wiki/b.md", mode: "merge", bytes: 8 }],
        },
      ],
    };
    const round = roundFromIngestDone(two, "Entropy");
    expect(round.written).toEqual([
      { path: "wiki/a.md", mode: "create" },
      { path: "wiki/b.md", mode: "merge" },
    ]);
    expect(round.intent).toBe("Entropy");
    expect(round.fromProject).toBe(false);
  });

  it("keeps the findings, and says when they were cut off", () => {
    const finding = { severity: "error", code: "x", message: "broken", path: "wiki/a.md" };
    const round = roundFromIngestDone(
      {
        ...done,
        results: [
          {
            ...done.results[0]!,
            doctor: { ...clean(), truncated: true, findings: [finding], findingCount: 40 },
          },
        ],
      },
      "Entropy",
    );
    expect(round.findings).toEqual([finding]);
    expect(round.truncated).toBe(true);
  });

  it("carries the files that could not be written", () => {
    const round = roundFromIngestDone(
      { ...done, errors: [{ path: "wiki/c.md", detail: "locked" }] },
      "Entropy",
    );
    expect(round.failures).toEqual([{ path: "wiki/c.md", detail: "locked" }]);
  });

  it("reports a page with no check as nothing rather than as a clean one", () => {
    const round = roundFromIngestDone(
      { ...done, results: [{ ...done.results[0]!, doctor: null }] },
      "",
    );
    expect(round.findings).toEqual([]);
    expect(round.truncated).toBe(false);
  });
});

describe("reading the round a project kept", () => {
  it("is the same round, marked as the project's", () => {
    const round = roundFromRecord(record);
    expect(round?.written).toEqual([{ path: "wiki/a.md", mode: "create" }]);
    expect(round?.intent).toBe("Entropy");
    expect(round?.fromProject).toBe(true);
  });

  it("says nothing at all for a project that has no round", () => {
    expect(roundFromRecord(null)).toBeNull();
  });

  it("invents no failures, since a refused write was reported when it happened", () => {
    // The record has no place for them: the round's report is what it wrote.
    const round = roundFromRecord({ ...record, writeWarnings: [] });
    expect(round?.failures).toEqual([]);
  });
});
