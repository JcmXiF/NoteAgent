/**
 * Classifies project paths.
 *
 * These predicates read only the path string, so they work for anything the API
 * returns before its bytes are loaded. Predicates that need a fetched payload
 * live in `capture.ts` instead, keeping the two input shapes separate.
 *
 * `rawBucket` returns an identifier rather than a label; the catalogue of
 * display names belongs to the UI layer.
 */

export type RawBucket = "web" | "pdf" | "office" | "media" | "other";

/** Ordered so the sidebar groups read predictably. */
export const RAW_BUCKETS: RawBucket[] = ["web", "pdf", "office", "media", "other"];

/** Strips Windows separators so paths coming from the API compare uniformly. */
function normalize(path: string): string {
  return path.replace(/\\/g, "/");
}

function extension(path: string, withDot = false): string {
  const clean = normalize(path).toLowerCase();
  const name = clean.slice(clean.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return "";
  return withDot ? name.slice(dot) : name.slice(dot + 1);
}

export function isHtmlPath(path: string): boolean {
  const ext = extension(path);
  return ext === "html" || ext === "htm";
}

export function isPdfPath(path: string): boolean {
  return extension(path) === "pdf";
}

export function isOfficePath(path: string): boolean {
  const ext = extension(path);
  return ext === "docx" || ext === "pptx";
}

export function isImagePath(path: string): boolean {
  return /^(png|jpe?g|gif|webp|svg|avif)$/.test(extension(path));
}

/** Files the document view can render as an original rather than as text. */
export function isRenderableOriginalPath(path: string): boolean {
  return (
    isHtmlPath(path) ||
    isPdfPath(path) ||
    isOfficePath(path) ||
    isImagePath(path)
  );
}

export function isRawSourcePath(path: string): boolean {
  return normalize(path).startsWith("raw/");
}

/**
 * Which kind a stored original is. The extension decides, because a source is
 * filed by what it is rather than by a subject; the folder is a second opinion
 * for a file whose extension says nothing, and an older project's deeper
 * `raw/sources/<kind>/` path is read the same way.
 */
export function rawBucket(path: string): RawBucket {
  if (isPdfPath(path)) return "pdf";
  if (isOfficePath(path)) return "office";
  if (isImagePath(path)) return "media";
  if (isHtmlPath(path)) return "web";

  // Unknown extension: trust the first folder naming a kind, if any.
  const clean = normalize(path);
  const prefix = "raw/";
  if (!clean.toLowerCase().startsWith(prefix)) return "other";
  const rest = clean.slice(prefix.length);
  const folders = rest.split("/").slice(0, -1);
  for (const folder of folders) {
    const kind = folder.toLowerCase() as RawBucket;
    if (RAW_BUCKETS.includes(kind) && kind !== "other") return kind;
  }
  return "other";
}

/* ------------------------------------------------------------------ wiki */

/** Pages maintained by the program; editing them by hand would be overwritten. */
const GENERATED_PAGES = new Set(["index.md", "overview.md", "log.md"]);

export function isGeneratedWikiPage(path: string | null | undefined): boolean {
  if (!path) return false;
  const base = normalize(path).split("/").pop() ?? "";
  return GENERATED_PAGES.has(base);
}

/**
 * A page that can carry citations and section bindings. Source summaries are
 * excluded because they summarise a whole file rather than citing passages.
 */
export function isBindableWikiPage(path: string | null | undefined): boolean {
  if (!path) return false;
  const clean = normalize(path);
  if (!clean.startsWith("wiki/") || !clean.endsWith(".md")) return false;
  if (isGeneratedWikiPage(clean)) return false;
  return !clean.startsWith("wiki/sources/");
}
