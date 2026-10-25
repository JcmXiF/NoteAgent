import { message, type Message } from "@/lib/message";
import type { CapturePreview, DeepSearchCard, ResourceCard } from "@/lib/api/types";

/**
 * Helpers for captured sources.
 *
 * A capture is what the backend returns after fetching a URL or reading a local
 * file: the extraction result, plus a fidelity verdict and reason codes. Nothing
 * here decides whether a capture counts as a source — the user does that — so
 * these are pure classification and formatting helpers.
 */

export function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url.trim());
}

/**
 * Whether an address is the identity a file was given instead of a URL.
 *
 * A file the reader brings in is not at any address, so the backend names it by its
 * bytes. That name is what a selection made from it carries, and re-adding the same
 * file produces the same name — which is the only way back to it.
 */
export function isLocalCaptureUrl(url: string): boolean {
  return url.trim().startsWith("local:");
}

/** How trustworthy the extraction was. `failed` means nothing usable came back. */
export function fidelityLabel(fidelity: string): Message {
  if (fidelity === "ok") return message("fidelity.ok");
  if (fidelity === "low") return message("fidelity.low");
  return message("fidelity.failed");
}

/**
 * Which colour a read result gets. Unknown values are shown as failures, which
 * matches what `fidelityLabel` tells the user.
 */
export function fidelityTone(fidelity: string): "success" | "warning" | "destructive" {
  if (fidelity === "ok") return "success";
  if (fidelity === "low") return "warning";
  return "destructive";
}

/** How the capture reached the workbench. */
export function intakeLabel(intake?: string): Message {
  switch (intake) {
    case "local":
      return message("intake.local");
    case "guest":
      return message("intake.guest");
    case "search":
      return message("intake.search");
    case "url":
      return message("intake.url");
    default:
      return message("intake.other");
  }
}

export function isPdfCapture(capture: CapturePreview): boolean {
  return capture.kind === "pdf" || Boolean(capture.pdfBase64);
}

export function isImageCapture(capture: CapturePreview): boolean {
  return capture.kind === "image" || Boolean(capture.imageBase64);
}

/**
 * A data URL for an image that has not been stored yet.
 *
 * An unsaved capture carries its bytes inline, so there is no URL to point at
 * until `raw/` has a copy of it.
 */
export function imageDataUrl(base64: string, mediaType?: string): string {
  const type =
    mediaType && mediaType.includes("/") ? mediaType : "application/octet-stream";
  return `data:${type};base64,${base64}`;
}

export function captureMatches(capture: CapturePreview, url: string): boolean {
  return capture.url === url || capture.finalUrl === url;
}

export function findCapture(
  captures: CapturePreview[],
  url: string,
): CapturePreview | undefined {
  return captures.find((item) => captureMatches(item, url));
}

/** Compares page URLs while ignoring a trailing slash and the fragment. */
export function samePageUrl(left: string, right: string): boolean {
  const normalize = (value: string) =>
    value.trim().replace(/\/+$/, "").split("#")[0] ?? "";
  return Boolean(left) && normalize(left) === normalize(right);
}

/**
 * Whether the page is only reachable through the guest browser, because an
 * anonymous request was rejected. Distinguishes "you need to sign in" from
 * "the extractor could not read it".
 */
export function needsBrowseCapture(capture: CapturePreview): boolean {
  if (capture.httpStatus === 401 || capture.httpStatus === 403) return true;
  return capture.reasons.some(
    (code) =>
      code === "http_unauthorized" ||
      code === "http_forbidden" ||
      code === "http_blocked",
  );
}

/**
 * One actionable sentence explaining why a capture failed, and what to try
 * instead. `hasGuestBrowser` selects between "open it in the built-in browser"
 * and "use the desktop app", which is the only difference the two environments
 * can act on.
 */
export function failedCaptureHint(
  capture: CapturePreview,
  hasGuestBrowser: boolean,
): Message {
  const reasons = new Set(capture.reasons);
  const signIn = hasGuestBrowser
    ? "captureHint.needsSignIn.guest"
    : "captureHint.needsSignIn.plain";

  if (reasons.has("http_unauthorized")) return message(signIn);
  if (reasons.has("http_forbidden") || reasons.has("http_blocked")) {
    return message(
      hasGuestBrowser ? "captureHint.blocked.guest" : "captureHint.blocked.plain",
    );
  }
  if (reasons.has("http_rate_limited")) {
    return message(
      hasGuestBrowser ? "captureHint.rateLimited.guest" : "captureHint.rateLimited.plain",
    );
  }
  // A page that came back all but empty reads very differently depending on how it
  // was read: one that was rendered can be finished loading next time, one that was
  // fetched cannot.
  if (reasons.has("too_short")) {
    return message(
      hasGuestBrowser ? "captureHint.tooShort.guest" : "captureHint.tooShort.plain",
    );
  }
  if (reasons.has("invalid_pdf")) return message("captureHint.invalidPdf");
  if (reasons.has("pdf_not_html")) return message("captureHint.pdfNotHtml");
  if (reasons.has("office_not_html")) return message("captureHint.officeNotHtml");
  if (reasons.has("invalid_office")) return message("captureHint.invalidOffice");
  if (reasons.has("invalid_image")) return message("captureHint.invalidImage");
  if (reasons.has("image_not_html")) return message("captureHint.imageNotHtml");
  if (reasons.has("adapter_not_ready")) return message("captureHint.notSupportedYet");
  if (reasons.has("unsupported_kind")) return message("captureHint.unknownFormat");
  if (reasons.has("invalid_payload")) return message("captureHint.unreadable");

  return message(
    hasGuestBrowser ? "captureHint.generic.guest" : "captureHint.generic.plain",
  );
}

/** The wire name for a capture that failed, in the interface's language. */
export function reasonText(code: string, t: (key: string) => string): string {
  const key = `reason.${code}`;
  const text = t(key);
  return text === key ? code : text;
}

/** Builds a search-result card from a capture, so both share one list item. */
export function cardFromCapture(
  capture: CapturePreview,
  t: (key: string) => string,
): ResourceCard {
  let host = "";
  if (isLocalCaptureUrl(capture.url)) {
    host = "";
  } else {
    try {
      host = new URL(capture.finalUrl || capture.url).hostname;
    } catch {
      host = "";
    }
  }

  const snippet =
    capture.markdown.replace(/\s+/g, " ").trim().slice(0, 120) ||
    capture.reasons.map((code) => reasonText(code, t)).join(" · ");

  return {
    title: capture.title || capture.url,
    url: capture.url,
    site: host,
    siteName: host,
    favicon: "",
    snippet,
    via: capture.captureMethod,
    why: "",
    intake: capture.intake,
  };
}

/**
 * Whether two captures describe the same page.
 *
 * `finalUrl` is compared only when both sides have one. The API fills it in
 * from the request URL, but a capture assembled elsewhere can leave it empty,
 * and two empty strings would otherwise look like a match.
 */
export function isSameCapture(left: CapturePreview, right: CapturePreview): boolean {
  if (left.url === right.url) return true;
  if (!left.finalUrl || !right.finalUrl) return false;
  return left.finalUrl === right.finalUrl;
}

/** Newest first, replacing an existing entry for the same page. */
export function mergeCaptures(
  current: CapturePreview[],
  incoming: CapturePreview[],
): CapturePreview[] {
  const next = [...current];
  for (const capture of incoming) {
    const index = next.findIndex((item) => isSameCapture(item, capture));
    if (index >= 0) next[index] = capture;
    else next.unshift(capture);
  }
  return next;
}

/** Tags search results so the list can show where a card came from. */
export function stampSearchCards(cards: ResourceCard[]): ResourceCard[] {
  return cards.map((card) => ({ ...card, intake: "search" }));
}

/**
 * Turns a card a deep search found into a workbench card.
 *
 * Kept as the card the round showed rather than rebuilt from the capture, so the
 * list does not reword itself the moment something is fetched. What the page it
 * was found for is kept in `why`, which is where the workbench looks for it.
 */
export function cardFromSearchResult(card: DeepSearchCard): ResourceCard {
  return {
    title: card.title || card.url,
    url: card.url,
    site: card.site,
    siteName: card.site,
    favicon: "",
    snippet: card.snippet,
    via: "deep-search",
    why: card.pageSlug,
    intake: "search",
  };
}
