import { isHttpUrl } from "@/lib/capture";
import { hostOf } from "@/lib/browse";
import { htmlHead, metaContent } from "@/lib/html-head";

/**
 * Where a read of a stored file says it came from, or null when nothing says.
 *
 * A captured page carries the address it was taken from in its own head, as a
 * meta tag; the file's own path says nothing about where it was. A PDF or Office
 * file carries it in the record beside it instead. Both arrive on the read, so
 * this is what a caller asks rather than what it works out twice.
 */
export function originUrlOf(file: {
  sourceUrl?: string | null;
  content?: string | null;
}): string | null {
  const recorded = file.sourceUrl?.trim();
  if (recorded && isHttpUrl(recorded)) return recorded;
  return sourceUrlFromHtml(file.content ?? null);
}

export function sourceUrlFromHtml(html: string | null): string | null {
  if (!html) return null;
  const url = metaContent(htmlHead(html), "noteagent:source_url")?.trim() ?? "";
  return isHttpUrl(url) ? url : null;
}

/**
 * Where a source came from, with the site named so it can be shown.
 *
 * The address is what a page is opened by; the site is what says which page is
 * being offered, and it is all that is shown when the whole address would be more
 * than the reader needs. Both come from the same address, so they travel together.
 */
export type SourceOrigin = {
  url: string;
  domain: string;
};

/** The origin an address names, or nothing when it is not a web address. */
export function sourceOriginFromUrl(url: string): SourceOrigin | null {
  return isHttpUrl(url) ? { url: url.trim(), domain: hostOf(url) } : null;
}
