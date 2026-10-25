import type { Note } from "@/lib/api/notes";
import type { Section } from "@/lib/sections";
import { normalizeProbe, sharedRun } from "@/lib/text-match";

/**
 * Where each remark goes on the page it was written about.
 *
 * A remark names its place in `anchor`: the page's own start or end, or the text
 * of a section heading. The heading is what a reader sees, so it is what someone
 * writing a remark can name — but a page is rewritten between rounds and a
 * heading may have been renamed since. `quote` is the way back: what was quoted
 * when the remark was written, looked for in the sections' prose when the heading
 * no longer matches. Neither is worth guessing past: a remark that cannot be
 * placed is reported as such rather than put somewhere plausible.
 *
 * The sections come from the reading the view renders, so a remark's place and
 * the text it sits beside cannot disagree.
 */

/** The anchor naming the page's own start, before its first section. */
export const ANCHOR_PREAMBLE = "::preamble";
/** The anchor naming the very end of the page. */
export const ANCHOR_END = "::end";

export type NotePlacement = {
  /** Remarks to show after the page's opening, before the first section. */
  preamble: Note[];
  /** One group per section, in the page's own order. */
  sections: Note[][];
  /** Remarks to show after everything else. */
  end: Note[];
  /** Remarks with no place on the page; shown so they are not simply lost. */
  unplaced: Note[];
};

/** Where on the page a remark belongs. */
type Place =
  | { kind: "preamble" }
  | { kind: "end" }
  | { kind: "section"; index: number }
  | { kind: "none" };

export function placeNotes(
  notes: Note[],
  body: string,
  sections: Section[],
): NotePlacement {
  const placement: NotePlacement = {
    preamble: [],
    sections: sections.map(() => []),
    end: [],
    unplaced: [],
  };
  // Oldest first, which is the order several remarks sharing one place are read
  // in. Equal timestamps keep the order they arrived in — the core's own, which
  // it breaks by name — so the two sides never disagree about what came first.
  const ordered = [...notes].sort((a, b) => a.created.localeCompare(b.created));
  for (const note of ordered) {
    const place = placeOf(note, body, sections);
    if (place.kind === "preamble") placement.preamble.push(note);
    else if (place.kind === "end") placement.end.push(note);
    else if (place.kind === "section") placement.sections[place.index].push(note);
    else placement.unplaced.push(note);
  }
  return placement;
}

function placeOf(note: Note, body: string, sections: Section[]): Place {
  const anchor = note.anchor.trim();
  if (!anchor) return { kind: "none" };
  if (anchor === ANCHOR_PREAMBLE) return { kind: "preamble" };
  if (anchor === ANCHOR_END) return { kind: "end" };
  // The heading's text is what was named, and the first heading carrying it is
  // the one that was meant.
  const named = sections.findIndex((section) => section.heading === anchor);
  if (named >= 0) return { kind: "section", index: named };
  const quoted = sectionQuoting(note.quote, body, sections);
  if (quoted >= 0) return { kind: "section", index: quoted };
  return { kind: "none" };
}

/**
 * The first section whose prose carries the quote, or `-1` when none does.
 *
 * The first and not the closest: a page repeats itself, and a quote found in two
 * sections says nothing about which was meant. A short quote is not distinctive
 * enough to be evidence of anything, so it counts as no match at all — the remark
 * then shows without a place rather than under a heading somebody else wrote.
 */
function sectionQuoting(quote: string, body: string, sections: Section[]): number {
  const probe = normalizeProbe(quote);
  if (!probe) return -1;
  for (let index = 0; index < sections.length; index += 1) {
    const { bodyStart, end } = sections[index];
    const hay = normalizeProbe(body.slice(bodyStart, end));
    if (hay && sharedRun(probe, hay)) return index;
  }
  return -1;
}
