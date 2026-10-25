import { describe, expect, it } from "vitest";
import {
  cardFromCapture,
  captureMatches,
  failedCaptureHint,
  fidelityLabel,
  findCapture,
  intakeLabel,
  isHttpUrl,
  isImageCapture,
  isPdfCapture,
  mergeCaptures,
  needsBrowseCapture,
  reasonText,
  samePageUrl,
  stampSearchCards,
  cardFromSearchResult,
} from "@/lib/capture";
import type { CapturePreview } from "@/lib/api/types";

function capture(overrides: Partial<CapturePreview> = {}): CapturePreview {
  // finalUrl defaults to the request URL, which is what the API returns; tests
  // that need a redirect override it explicitly.
  const url = overrides.url ?? "https://example.com/a";
  return {
    url,
    finalUrl: url,
    title: "Example",
    markdown: "Readable body text.",
    captureMethod: "http",
    fidelity: "ok",
    reasons: [],
    httpStatus: 200,
    charCount: 20,
    ...overrides,
  };
}

describe("isHttpUrl", () => {
  it("accepts http and https only", () => {
    expect(isHttpUrl("https://a.com")).toBe(true);
    expect(isHttpUrl("  http://a.com  ")).toBe(true);
    expect(isHttpUrl("local:file.pdf")).toBe(false);
    expect(isHttpUrl("file:///c:/x.pdf")).toBe(false);
  });
});

describe("labels", () => {
  it("maps fidelity, defaulting anything unknown to failed", () => {
    expect(fidelityLabel("ok").key).toBe("fidelity.ok");
    expect(fidelityLabel("low").key).toBe("fidelity.low");
    expect(fidelityLabel("failed").key).toBe("fidelity.failed");
    expect(fidelityLabel("something-new").key).toBe("fidelity.failed");
  });

  it("maps intake, defaulting to other", () => {
    expect(intakeLabel("local").key).toBe("intake.local");
    expect(intakeLabel("guest").key).toBe("intake.guest");
    expect(intakeLabel("search").key).toBe("intake.search");
    expect(intakeLabel("url").key).toBe("intake.url");
    expect(intakeLabel(undefined).key).toBe("intake.other");
    expect(intakeLabel("mystery").key).toBe("intake.other");
  });
});

describe("classification", () => {
  it("detects PDFs by kind or by payload", () => {
    expect(isPdfCapture(capture({ kind: "pdf" }))).toBe(true);
    expect(isPdfCapture(capture({ pdfBase64: "AAAA" }))).toBe(true);
    expect(isPdfCapture(capture())).toBe(false);
  });

  it("detects images by kind or by payload", () => {
    expect(isImageCapture(capture({ kind: "image" }))).toBe(true);
    expect(isImageCapture(capture({ imageBase64: "AAAA" }))).toBe(true);
    expect(isImageCapture(capture())).toBe(false);
  });

  it("matches a capture against either of its URLs", () => {
    expect(captureMatches(capture(), "https://example.com/a")).toBe(true);
    expect(
      captureMatches(capture({ finalUrl: "https://example.com/final" }), "https://example.com/final"),
    ).toBe(true);
    expect(captureMatches(capture(), "https://example.com/b")).toBe(false);
  });

  it("finds a capture by URL", () => {
    const list = [capture({ url: "https://a.com" }), capture({ url: "https://b.com" })];
    expect(findCapture(list, "https://b.com")?.url).toBe("https://b.com");
    expect(findCapture(list, "https://c.com")).toBeUndefined();
  });

  it("compares page URLs ignoring a trailing slash and fragment", () => {
    expect(samePageUrl("https://a.com/x/", "https://a.com/x#part")).toBe(true);
    expect(samePageUrl("https://a.com/x", "https://a.com/y")).toBe(false);
    expect(samePageUrl("", "")).toBe(false);
  });

  it("decides when the guest browser is the only way in", () => {
    expect(needsBrowseCapture(capture({ httpStatus: 401 }))).toBe(true);
    expect(needsBrowseCapture(capture({ httpStatus: 403 }))).toBe(true);
    expect(needsBrowseCapture(capture({ reasons: ["http_blocked"] }))).toBe(true);
    expect(needsBrowseCapture(capture({ httpStatus: 429 }))).toBe(false);
    expect(needsBrowseCapture(capture())).toBe(false);
  });
});

describe("failedCaptureHint", () => {
  /** Reason code → key stem. Only the first four vary by environment. */
  const environmentVariants: [string, string][] = [
    ["http_unauthorized", "captureHint.needsSignIn"],
    ["http_forbidden", "captureHint.blocked"],
    ["http_blocked", "captureHint.blocked"],
    ["http_rate_limited", "captureHint.rateLimited"],
    ["too_short", "captureHint.tooShort"],
  ];
  const singleVariant: [string, string][] = [
    ["invalid_pdf", "captureHint.invalidPdf"],
    ["pdf_not_html", "captureHint.pdfNotHtml"],
    ["office_not_html", "captureHint.officeNotHtml"],
    ["invalid_office", "captureHint.invalidOffice"],
    ["invalid_image", "captureHint.invalidImage"],
    ["image_not_html", "captureHint.imageNotHtml"],
    ["adapter_not_ready", "captureHint.notSupportedYet"],
    ["unsupported_kind", "captureHint.unknownFormat"],
    ["invalid_payload", "captureHint.unreadable"],
  ];

  it.each(environmentVariants)(
    "maps %s to %s, with a guest and a non-guest wording",
    (reason, key) => {
      expect(failedCaptureHint(capture({ reasons: [reason] }), true).key).toBe(`${key}.guest`);
      expect(failedCaptureHint(capture({ reasons: [reason] }), false).key).toBe(`${key}.plain`);
    },
  );

  it.each(singleVariant)("maps %s to %s in both environments", (reason, key) => {
    expect(failedCaptureHint(capture({ reasons: [reason] }), true).key).toBe(key);
    expect(failedCaptureHint(capture({ reasons: [reason] }), false).key).toBe(key);
  });

  it("falls back to a generic hint for unknown reason codes", () => {
    expect(failedCaptureHint(capture({ reasons: ["brand_new"] }), true).key).toBe(
      "captureHint.generic.guest",
    );
    expect(failedCaptureHint(capture({ reasons: ["brand_new"] }), false).key).toBe(
      "captureHint.generic.plain",
    );
    expect(failedCaptureHint(capture(), true).key).toBe("captureHint.generic.guest");
  });

  it("resolves codes independently of their order", () => {
    expect(
      failedCaptureHint(capture({ reasons: ["unknown_first", "invalid_pdf"] }), true).key,
    ).toBe("captureHint.invalidPdf");
  });

  it("prefers the sign-in advice when a page is both refused and thin", () => {
    // A sign-in wall returns a stub page, so both codes can arrive together; the
    // one that says what to do about it is the sign-in.
    expect(
      failedCaptureHint(capture({ reasons: ["too_short", "http_unauthorized"] }), true).key,
    ).toBe("captureHint.needsSignIn.guest");
  });
});

describe("reasonText", () => {
  it("uses the catalog when it has the code", () => {
    expect(reasonText("no_text_layer", (key) => `${key}!`)).toBe("reason.no_text_layer!");
  });

  it("shows the code itself when the catalog does not", () => {
    expect(reasonText("something_new", (key) => key)).toBe("something_new");
  });
});

describe("cardFromCapture", () => {
  // The interface words a reason; the core sends only its code.
  const t = (key: string) => (key === "reason.no_text_layer" ? "No text layer" : key);

  it("takes the host from the final URL", () => {
    const card = cardFromCapture(
      capture({ finalUrl: "https://blog.example.org/post/1", url: "https://t.co/x" }),
      t,
    );
    expect(card.site).toBe("blog.example.org");
    expect(card.siteName).toBe("blog.example.org");
  });

  it("leaves the host empty for local files, which the intake label covers", () => {
    expect(cardFromCapture(capture({ url: "local:a.pdf", finalUrl: "" }), t).site).toBe("");
  });

  it("summarises the body, falling back to the failure reasons", () => {
    expect(cardFromCapture(capture({ markdown: "  Body\n\nwith   space " }), t).snippet).toBe(
      "Body with space",
    );
    expect(
      cardFromCapture(capture({ markdown: "", reasons: ["no_text_layer"] }), t).snippet,
    ).toBe("No text layer");
  });

  it("falls back to the URL when there is no title", () => {
    expect(cardFromCapture(capture({ title: "" }), t).title).toBe("https://example.com/a");
  });
});

describe("mergeCaptures", () => {
  it("puts new captures first and replaces an existing page in place", () => {
    const merged = mergeCaptures(
      [capture({ url: "https://a.com/1", title: "one" }), capture({ url: "https://a.com/2", title: "two" })],
      [capture({ url: "https://a.com/3", title: "three" }), capture({ url: "https://a.com/2", title: "two again" })],
    );
    expect(merged.map((item) => item.title)).toEqual(["three", "one", "two again"]);
  });

  it("does not collapse entries whose finalUrl is empty", () => {
    const local = (url: string) => capture({ url, finalUrl: "", title: url });
    const merged = mergeCaptures(
      [local("local:a.pdf"), local("local:b.pdf")],
      [local("local:c.pdf")],
    );
    expect(merged.map((item) => item.url)).toEqual([
      "local:c.pdf",
      "local:a.pdf",
      "local:b.pdf",
    ]);
  });

  it("still merges two request URLs that landed on the same page", () => {
    const merged = mergeCaptures(
      [capture({ url: "https://a.com/x", finalUrl: "https://a.com/final" })],
      [capture({ url: "https://a.com/y", finalUrl: "https://a.com/final", title: "settled" })],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe("settled");
  });
});

describe("cardFromSearchResult", () => {
  const searchCard = {
    pageSlug: "concepts/entropy",
    title: "What is entropy",
    url: "https://example.com/e",
    snippet: "A first look.",
    site: "example.com",
    picked: true,
  };

  it("keeps the card the round showed, and says which page it was found for", () => {
    const card = cardFromSearchResult(searchCard);
    expect(card).toEqual({
      title: "What is entropy",
      url: "https://example.com/e",
      site: "example.com",
      siteName: "example.com",
      favicon: "",
      snippet: "A first look.",
      via: "deep-search",
      why: "concepts/entropy",
      intake: "search",
    });
  });

  it("falls back to the address when a card has no title", () => {
    expect(cardFromSearchResult({ ...searchCard, title: "" }).title).toBe(
      "https://example.com/e",
    );
  });
});

describe("stampSearchCards", () => {
  it("tags cards so the list can show where they came from", () => {
    const cards = stampSearchCards([
      { title: "T", url: "https://a.com", site: "a.com", siteName: "a.com", favicon: "", snippet: "", via: "", why: "" },
    ]);
    expect(cards[0].intake).toBe("search");
  });
});
