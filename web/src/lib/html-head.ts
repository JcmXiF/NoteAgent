/**
 * Reading the head of a captured page.
 *
 * A saved page records what it is in its own head — the address it came from, and
 * the name it had — so reading those back is how the app can say anything about a
 * file whose name is the address. Only the head is searched, and only the first
 * slice of a page whose head never closes, so looking does not mean scanning a
 * whole document.
 */

/** How much of a page to look at when its head never closes. */
const HEAD_LIMIT = 16_384;

/** The head of a page, or as much of its start as a headless page gets. */
export function htmlHead(html: string): string {
  const end = html.search(/<\/head>/i);
  return end >= 0 ? html.slice(0, end) : html.slice(0, HEAD_LIMIT);
}

/** Turns the entities a writer escapes into the characters they stand for. */
function decodeAttribute(value: string): string {
  return value
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&");
}

/** One attribute of a tag, whichever case or quote style the writer used. */
function attributeOf(tag: string, name: string): string | null {
  const match = new RegExp(`\\b${name}\\s*=\\s*(["'])([\\s\\S]*?)\\1`, "i").exec(tag);
  return match?.[2] ?? null;
}

/** What a `<meta name="...">` in the head says, decoded. */
export function metaContent(head: string, name: string): string | null {
  for (const tag of head.match(/<meta\b[^>]*>/gi) ?? []) {
    if (attributeOf(tag, "name")?.trim().toLowerCase() !== name.toLowerCase()) continue;
    return decodeAttribute(attributeOf(tag, "content") ?? "");
  }
  return null;
}

/**
 * The page's own name, as its head gives it.
 *
 * Escapes are decoded and runs of whitespace collapsed, because a head may carry
 * a title across lines and the reader only ever sees one line of it.
 */
export function titleOfHead(head: string): string | null {
  const match = /<title\b[^>]*>([\s\S]*?)<\/title>/i.exec(head);
  if (!match) return null;
  const title = decodeAttribute(match[1] ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return title || null;
}
