import { describe, expect, it } from "vitest";
import {
  capEntries,
  clearIndex,
  entryFromCapture,
  indexKey,
  mergeEntries,
  readIndex,
  STORED_ENTRY_LIMIT,
  writeIndex,
  writtenPathsFrom,
  type CaptureEntry,
} from "@/lib/acquire-index";
import type { StorageLike } from "@/lib/storage";
import type { CapturePreview } from "@/lib/api/types";

function memoryStorage(seed: Record<string, string> = {}): StorageLike & {
  dump: () => Record<string, string>;
} {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
    dump: () => Object.fromEntries(map),
  };
}

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

describe("indexKey", () => {
  it("namespaces per project", () => {
    expect(indexKey("wiki-one")).toBe("noteagent.captures.wiki-one");
    expect(indexKey("wiki-one")).not.toBe(indexKey("wiki-two"));
  });
});

describe("write and read", () => {
  it("round-trips the record without the captured text", () => {
    const storage = memoryStorage();
    writeIndex("p", [entryFromCapture(capture({ kind: "html" }), 123)], storage);

    const stored = JSON.parse(storage.dump()[indexKey("p")]);
    expect(stored).toEqual([
      {
        url: "https://example.com/a",
        title: "Example",
        fidelity: "ok",
        kind: "html",
        capturedAt: 123,
      },
    ]);
    // The extracted text must not be in storage.
    expect(storage.dump()[indexKey("p")]).not.toContain("markdown");
    expect(storage.dump()[indexKey("p")]).not.toContain("Body");
  });

  it("restores the order it was given", () => {
    const storage = memoryStorage();
    writeIndex(
      "p",
      [
        entryFromCapture(capture({ url: "https://a.com" }), 2),
        entryFromCapture(capture({ url: "https://b.com" }), 1),
      ],
      storage,
    );
    expect(readIndex("p", storage).map((entry) => entry.url)).toEqual([
      "https://a.com",
      "https://b.com",
    ]);
  });

  it("keeps the stored path so the list can link to the original", () => {
    const storage = memoryStorage();
    const entry: CaptureEntry = {
      ...entryFromCapture(capture()),
      path: "raw/web/a.html",
    };
    writeIndex("p", [entry], storage);
    expect(readIndex("p", storage)[0].path).toBe("raw/web/a.html");
  });

  it("drops entries that are missing an identity", () => {
    const storage = memoryStorage({
      [indexKey("p")]: JSON.stringify([
        { url: "https://ok.com" },
        { url: "" },
        { title: "no url" },
        null,
        "a string",
        { url: 42 },
      ]),
    });
    const entries = readIndex("p", storage);
    expect(entries).toHaveLength(1);
    expect(entries[0].url).toBe("https://ok.com");
    // Missing fields fall back rather than becoming undefined.
    expect(entries[0].title).toBe("");
    expect(entries[0].capturedAt).toBe(0);
  });

  it("survives a corrupt value instead of throwing", () => {
    const storage = memoryStorage({ [indexKey("p")]: "{not json" });
    expect(readIndex("p", storage)).toEqual([]);
    const notAList = memoryStorage({ [indexKey("p")]: '{"url":"x"}' });
    expect(readIndex("p", notAList)).toEqual([]);
  });

  it("removes the key rather than storing an empty list", () => {
    const storage = memoryStorage();
    writeIndex("p", [entryFromCapture(capture())], storage);
    writeIndex("p", [], storage);
    expect(storage.dump()[indexKey("p")]).toBeUndefined();
    expect(readIndex("p", storage)).toEqual([]);
  });

  it("clears a project's list", () => {
    const storage = memoryStorage();
    writeIndex("p", [entryFromCapture(capture())], storage);
    clearIndex("p", storage);
    expect(readIndex("p", storage)).toEqual([]);
  });

  it("does nothing without storage or a project", () => {
    expect(readIndex("", memoryStorage())).toEqual([]);
    expect(() => writeIndex("p", [entryFromCapture(capture())], null)).not.toThrow();
    expect(readIndex("p", null)).toEqual([]);
  });
});

describe("mergeEntries", () => {
  const first: CaptureEntry = {
    ...entryFromCapture(capture({ url: "https://a.com", title: "old" }), 1),
  };

  it("puts new entries first", () => {
    const merged = mergeEntries(
      [first],
      [entryFromCapture(capture({ url: "https://b.com" }), 2)],
    );
    expect(merged.map((entry) => entry.url)).toEqual(["https://b.com", "https://a.com"]);
  });

  it("replaces an entry for the same page instead of duplicating it", () => {
    const merged = mergeEntries(
      [first],
      [entryFromCapture(capture({ url: "https://a.com", title: "new" }), 2)],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe("new");
  });

  it("recognises the same page reached through a redirect", () => {
    const merged = mergeEntries(
      [
        {
          ...entryFromCapture(
            capture({ url: "https://a.com/x", finalUrl: "https://a.com/final" }),
          ),
          path: "raw/web/a.html",
        },
      ],
      [
        entryFromCapture(
          capture({ url: "https://a.com/y", finalUrl: "https://a.com/final" }),
        ),
      ],
    );
    expect(merged).toHaveLength(1);
    // Re-fetching does not know about the stored original, so it is kept.
    expect(merged[0].path).toBe("raw/web/a.html");
  });

  it("does not merge entries that have no captures to compare", () => {
    const restored: CaptureEntry = {
      url: "https://a.com",
      title: "restored",
      fidelity: "ok",
      kind: "",
      capturedAt: 1,
    };
    const merged = mergeEntries([restored], [
      entryFromCapture(capture({ url: "https://a.com", title: "fetched" }), 2),
    ]);
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe("fetched");
  });
});

describe("the stored list's bound", () => {
  function entry(url: string, capturedAt: number): CaptureEntry {
    return { url, title: url.split("/").pop() ?? url, fidelity: "ok", kind: "", capturedAt };
  }

  function entries(count: number, base = 1_000): CaptureEntry[] {
    return Array.from({ length: count }, (_, i) => entry(`https://a.com/t${i}`, base + i));
  }

  it("keeps the newest and drops the least recently captured", () => {
    const capped = capEntries(entries(5), 2);
    expect(capped.map((item) => item.title)).toEqual(["t3", "t4"]);
  });

  it("leaves the survivors in the order the reader was seeing", () => {
    // The list is what is on screen; dropping rows must not shuffle the rest, so
    // the older of the two survivors stays above the newer one.
    const onScreen = [entry("https://a.com/t1", 1_001), entry("https://a.com/t0", 1_000), entry("https://a.com/t2", 1_002)];
    expect(capEntries(onScreen, 2).map((item) => item.title)).toEqual(["t1", "t2"]);
  });

  it("counts how recently a source was captured, not where it sits", () => {
    // Re-fetching an old source keeps it: it is not the oldest any more.
    const list = [
      entry("https://a.com/old", 5),
      entry("https://a.com/new", 100),
    ];
    expect(capEntries(list, 1).map((item) => item.title)).toEqual(["new"]);
  });

  it("leaves a list that is within its bound alone", () => {
    const list = entries(3);
    expect(capEntries(list, 10)).toBe(list);
  });

  it("applies the bound as the list grows, so memory and storage agree", () => {
    const full = entries(STORED_ENTRY_LIMIT);
    const merged = mergeEntries(full, [entry("https://a.com/one-more", 1_000_000)]);
    expect(merged).toHaveLength(STORED_ENTRY_LIMIT);
    // The one just captured is there; the oldest is what gave up its place.
    expect(merged.some((item) => item.url === "https://a.com/one-more")).toBe(true);
    expect(merged.some((item) => item.url === "https://a.com/t0")).toBe(false);
  });

  it("is applied when the list is written, whatever it was handed", () => {
    const storage = memoryStorage();
    writeIndex("p", entries(STORED_ENTRY_LIMIT + 10), storage);
    const stored = JSON.parse(storage.dump()[indexKey("p")] ?? "[]") as CaptureEntry[];
    expect(stored).toHaveLength(STORED_ENTRY_LIMIT);
    expect(stored[0]?.url).toBe("https://a.com/t10");
  });

  it("is applied when the list is read back", () => {
    const storage = memoryStorage({ [indexKey("p")]: JSON.stringify(entries(4)) });
    expect(readIndex("p", storage)).toHaveLength(4);
    const many = memoryStorage({
      [indexKey("p")]: JSON.stringify(entries(STORED_ENTRY_LIMIT + 10)),
    });
    expect(readIndex("p", many)).toHaveLength(STORED_ENTRY_LIMIT);
  });
});

describe("writtenPathsFrom", () => {
  it("maps both the request URL and the settled URL to the stored path", () => {
    const paths = writtenPathsFrom([
      {
        ...entryFromCapture(
          capture({ url: "https://a.com/x", finalUrl: "https://a.com/final" }),
        ),
        path: "raw/web/a.html",
      },
    ]);
    expect(paths).toEqual({
      "https://a.com/x": "raw/web/a.html",
      "https://a.com/final": "raw/web/a.html",
    });
  });

  it("ignores entries that have not been stored", () => {
    expect(writtenPathsFrom([entryFromCapture(capture())])).toEqual({});
  });
});
