import { htmlHead, titleOfHead } from "@/lib/html-head";
import { isRawSourcePath } from "@/lib/source-kind";
import type { TreeFile } from "@/lib/project-tree";

/**
 * What a stored file is called.
 *
 * A stored original is filed under the address it came from plus a code suffix, so
 * its name is often an address leaf — a number, a UUID — rather than a name. The
 * core reads the name the document carries and sends it with the file list; what is
 * left for the interface is the other half: when a document carries no name of its
 * own, a name is read off the file it was stored as.
 */

/** The suffix the writer adds to keep an address's file from colliding. */
const CODE_SUFFIX = /-[0-9a-f]{12}$/i;

/** Extensions that say what a file is, not what it is called. */
const ADDRESS_EXTENSION =
  /\.(md|html?|pdf|docx|pptx|png|jpe?g|gif|webp|svg|avif)$/i;

/**
 * A name worth showing, from the one the file was stored under.
 *
 * It drops the extension, the code suffix, and an extension the address leaf left
 * behind, so `熵_(信息论)-dd3c946915be.html` reads as `熵_(信息论)` and
 * `12906163.html-9dd0e3d5e910.html` as `12906163`. A name that is only its address
 * leaf stays that leaf: there is nothing better to be had from a name.
 */
export function readableName(value: string): string {
  const name = value.replace(/\\/g, "/").split("/").pop() ?? value;
  const trimmed = name
    .replace(ADDRESS_EXTENSION, "")
    .replace(CODE_SUFFIX, "")
    .replace(ADDRESS_EXTENSION, "")
    .trim();
  return trimmed || name;
}

/** An address is not a name: a page that gives one gives no title. */
const ADDRESS_TITLE = /^https?:\/\//i;

/**
 * The name a captured page gives itself, or null when its head gives none.
 *
 * The core reads this too, and sends it with the file list; this is for what the
 * core could not read. It answers the same way, including treating a page that
 * offers its own address as a page without a title — the writer falls back to the
 * address, and the file name says more.
 */
export function readableTitleFromHtml(html: string | null): string | null {
  if (!html) return null;
  const title = titleOfHead(htmlHead(html));
  if (!title || ADDRESS_TITLE.test(title)) return null;
  return title;
}

/**
 * The file list with a name on every stored file.
 *
 * Only the stored files are touched: a page's own name is its slug, and that is
 * what the graph and every link to it are written in terms of. A document's own
 * name comes from the core and is kept as it is — it is what the document says it
 * is, and cleaning it up would only damage it. The file name is the fallback, for
 * the kinds that carry no name at all (an image) and for anything the core could
 * not read.
 */
export function readableTitles(files: TreeFile[]): TreeFile[] {
  return files.map((file) =>
    isRawSourcePath(file.path) ? { ...file, title: file.title || readableName(file.name) } : file,
  );
}
