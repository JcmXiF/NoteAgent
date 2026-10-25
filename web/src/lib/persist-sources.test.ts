import { describe, expect, it } from "vitest";
import {
  MissingSourcesError,
  SourceNotStoredError,
  capturesForCollections,
  collectionsNeedingStorage,
  remapPairsFromSave,
  summarizeSave,
  writtenPathRecords,
} from "@/lib/persist-sources";
import { previewSourceKey, type BlockCollection } from "@/lib/clips";
import type { CapturePreview, RawSaveItem } from "@/lib/api/types";

function capture(overrides: Partial<CapturePreview> = {}): CapturePreview {
  const url = overrides.url ?? "https://example.com/a";
  return {
    url,
    finalUrl: url,
    title: "Example",
    markdown: "Body",
    captureMethod: "http",
    fidelity: "ok",
    reasons: [],
    httpStatus: 200,
    charCount: 4,
    ...overrides,
  };
}

function collection(overrides: Partial<BlockCollection> = {}): BlockCollection {
  return {
    id: "col-1",
    project: "demo",
    sourceKey: previewSourceKey("https://example.com/a"),
    sourceLabel: "Example",
    blockIds: ["b1"],
    blocks: [],
    ...overrides,
  };
}

function saveItem(overrides: Partial<RawSaveItem> = {}): RawSaveItem {
  return {
    code: "written",
    path: "raw/web/a.html",
    url: "https://example.com/a",
    message: "",
    ...overrides,
  };
}

describe("collectionsNeedingStorage", () => {
  it("keeps only the selections whose original is not stored", () => {
    const pending = collectionsNeedingStorage([
      collection({ id: "a" }),
      collection({ id: "b", sourceKey: "raw/web/a.html" }),
      collection({ id: "c", sourceKey: previewSourceKey("https://b.com") }),
    ]);
    expect(pending.map((item) => item.id)).toEqual(["a", "c"]);
  });

  it("returns nothing when everything is stored", () => {
    expect(
      collectionsNeedingStorage([collection({ sourceKey: "raw/web/a.html" })]),
    ).toEqual([]);
  });
});

describe("capturesForCollections", () => {
  it("finds the capture behind each preview selection", () => {
    const found = capturesForCollections(
      [collection()],
      [capture(), capture({ url: "https://other.com" })],
    );
    expect(found.captures.map((item) => item.url)).toEqual(["https://example.com/a"]);
    expect(found.missing).toEqual([]);
  });

  it("matches a capture by the URL it settled on", () => {
    const found = capturesForCollections(
      [collection()],
      [capture({ url: "https://t.co/x", finalUrl: "https://example.com/a" })],
    );
    expect(found.captures).toHaveLength(1);
  });

  it("reports a selection whose capture is gone", () => {
    const found = capturesForCollections([collection()], []);
    expect(found.captures).toEqual([]);
    expect(found.missing).toEqual(["Example"]);
  });

  it("treats a failed read as nothing to store", () => {
    const found = capturesForCollections(
      [collection()],
      [capture({ fidelity: "failed" })],
    );
    expect(found.captures).toEqual([]);
    expect(found.missing).toEqual(["Example"]);
  });

  it("names the URL when the selection has no label", () => {
    const found = capturesForCollections(
      [collection({ sourceLabel: "" })],
      [],
    );
    expect(found.missing).toEqual(["https://example.com/a"]);
  });

  it("stores a page once even when two selections came from it", () => {
    const found = capturesForCollections(
      [collection({ id: "a" }), collection({ id: "b" })],
      [capture()],
    );
    expect(found.captures).toHaveLength(1);
  });

  it("ignores selections that are already stored", () => {
    const found = capturesForCollections(
      [collection({ sourceKey: "raw/web/a.html" })],
      [capture()],
    );
    expect(found.captures).toEqual([]);
    expect(found.missing).toEqual([]);
  });
});

describe("remapPairsFromSave", () => {
  it("rewrites the preview key to the stored path", () => {
    expect(remapPairsFromSave([saveItem()], [capture()])).toEqual([
      { fromKey: previewSourceKey("https://example.com/a"), toKey: "raw/web/a.html" },
    ]);
  });

  it("rewrites both URLs when the page was redirected", () => {
    const pairs = remapPairsFromSave(
      [saveItem({ url: "https://example.com/a" })],
      [capture({ url: "https://example.com/a", finalUrl: "https://example.com/final" })],
    );
    const keys = pairs.map((pair) => pair.fromKey);
    expect(keys).toContain(previewSourceKey("https://example.com/a"));
    expect(keys).toContain(previewSourceKey("https://example.com/final"));
    expect(pairs.every((pair) => pair.toKey === "raw/web/a.html")).toBe(true);
  });

  it("skips an item that was not stored", () => {
    expect(remapPairsFromSave([saveItem({ path: "" })], [capture()])).toEqual([]);
  });

  it("does not repeat a rewrite", () => {
    const pairs = remapPairsFromSave([saveItem(), saveItem()], [capture()]);
    expect(pairs).toHaveLength(1);
  });

  it("ignores items that match no capture", () => {
    expect(
      remapPairsFromSave([saveItem({ url: "https://unrelated.com" })], [capture()]),
    ).toEqual([]);
  });
});

describe("writtenPathRecords", () => {
  it("records the stored path for the page", () => {
    expect(writtenPathRecords([saveItem()], [capture()])).toEqual([
      { url: "https://example.com/a", path: "raw/web/a.html" },
    ]);
  });

  it("records it under both URLs after a redirect", () => {
    const records = writtenPathRecords(
      [saveItem()],
      [capture({ url: "https://example.com/a", finalUrl: "https://example.com/final" })],
    );
    expect(records.map((item) => item.url).sort()).toEqual([
      "https://example.com/a",
      "https://example.com/final",
    ]);
  });

  it("skips items that were not stored", () => {
    expect(writtenPathRecords([saveItem({ path: "" })], [capture()])).toEqual([]);
  });

  it("ignores items that match no capture, so nothing is misattributed", () => {
    expect(
      writtenPathRecords([saveItem({ url: "https://unrelated.com" })], [capture()]),
    ).toEqual([]);
  });
});

describe("summarizeSave", () => {
  it("separates what was written from what was refused", () => {
    const result = summarizeSave([
      saveItem(),
      saveItem({ code: "skipped", path: "", message: "too large" }),
    ]);
    expect(result.written).toHaveLength(1);
    expect(result.refused.map((item) => item.message)).toEqual(["too large"]);
  });

  it("handles an empty response", () => {
    expect(summarizeSave([])).toEqual({ written: [], refused: [] });
  });
});

describe("errors", () => {
  it("carries the labels that could not be stored", () => {
    const err = new MissingSourcesError(["Page one", "Page two"]);
    expect(err.labels).toEqual(["Page one", "Page two"]);
    expect(err).toBeInstanceOf(Error);
  });

  it("carries the backend's explanation", () => {
    const err = new SourceNotStoredError("blocked");
    expect(err.detail).toBe("blocked");
  });
});
