import { stripClipMarkers } from "@/lib/wiki-evidence";
import { splitWikiFrontmatter } from "@/lib/wiki-locators";

/**
 * A page's prose, as everything that reads a page reads it.
 *
 * The frontmatter is structured data rather than prose, and the pipeline's marker
 * comments say which selection a section came from, so neither belongs in the
 * text anything reads. Both are taken off here, in one place, because two readers
 * who trim differently disagree about what a page says — and the remarks screen
 * and the reading surface have to agree, since a remark placed by one is drawn by
 * the other.
 */
export function pageBody(raw: string): string {
  return stripClipMarkers(splitWikiFrontmatter(raw).body).trim();
}
