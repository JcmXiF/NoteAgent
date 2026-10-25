import { ANCHOR_END, ANCHOR_PREAMBLE } from "@/lib/note-placement";
import { readSections, type Section } from "@/lib/sections";
import { normalizeProbe } from "@/lib/text-match";

/**
 * The places a remark can be put, and what to write when one is picked.
 *
 * The reading surface puts a remark where its `anchor` says, and the reading of
 * that lives in `note-placement`. This is the other half of the same pair: what
 * the remarks screen offers, and what it writes down when a place is chosen. The
 * two have to agree about what an anchor means, so the sentinels are named once,
 * in `note-placement`, and read from there.
 *
 * A quote is written down when a section is picked, because it is the way back to
 * that section after the heading has been renamed: the picker is the only place
 * where the section's prose and the choice are both in hand.
 *
 * Pure text handling: no fetching, no React, no i18n.
 */

/** What a picked place is called, before any wording is put on it. */
type PlacementKind = "preamble" | "section" | "end" | "none";

/** One place a remark can be put. */
export type Placement = {
  kind: PlacementKind;
  /** What goes into `anchor`. */
  anchor: string;
  /** What goes into `quote`; empty when there is nothing to stand behind it. */
  quote: string;
  /** A section's heading, so the picker can name it. */
  heading?: string;
  /** A section's depth, so the list reads in the page's own shape. */
  level?: number;
};

/**
 * A quote shorter than this says nothing about which section was meant, and the
 * reading side treats it as no match at all, so it is not worth keeping.
 */
const MIN_QUOTE = 16;

/**
 * How much of a section's opening to keep.
 *
 * A quote is evidence and not a copy of the section: enough to be recognised is
 * the whole point, and keeping the whole passage would put a second copy of the
 * page into the remark's own file.
 */
const MAX_QUOTE = 200;

/**
 * Every place the picker offers for a page, in reading order.
 *
 * The page's own start and end are always there — a remark at either is about the
 * page rather than about a passage, so a page whose text cannot be read can still
 * be remarked on. What is missing when the text cannot be read is the sections,
 * since which ones a page has is only known from the text.
 */
export function placements(body: string): Placement[] {
  const { sections } = readSections(body);
  const out: Placement[] = [
    { kind: "preamble", anchor: ANCHOR_PREAMBLE, quote: "" },
  ];
  for (const section of sections) {
    out.push({
      kind: "section",
      anchor: section.heading,
      quote: openingQuote(body, section),
      heading: section.heading,
      level: section.level,
    });
  }
  out.push({ kind: "end", anchor: ANCHOR_END, quote: "" });
  out.push({ kind: "none", anchor: "", quote: "" });
  return out;
}

/**
 * The opening of a section, as it was written, for use as a quote.
 *
 * Verbatim rather than tidied: the reading side looks for these words in the
 * page, and a quote that had been rewritten to look neat would be one nobody
 * wrote. Empty when the section opens with too little to recognise.
 */
function openingQuote(body: string, section: Section): string {
  const prose = body.slice(section.bodyStart, section.end).trim();
  if (!prose) return "";
  const paragraph = (prose.split(/\n\s*\n/, 1)[0] ?? "").trim();
  const quote = paragraph.length > MAX_QUOTE ? paragraph.slice(0, MAX_QUOTE).trim() : paragraph;
  return normalizeProbe(quote).length >= MIN_QUOTE ? quote : "";
}
