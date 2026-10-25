/**
 * Deciding whether two stretches of prose are the same passage.
 *
 * Two callers ask the same question of text neither of them wrote — "is this the
 * passage that was meant?" — so the judgement lives here rather than being
 * written out twice. One is tying a page's section to the selection it was
 * written from; the other is finding where a remark goes when the heading it
 * named has since been renamed. Both are looking at prose that may have been
 * trimmed, reflowed or partly rewritten since.
 *
 * The comparison is loose on purpose: case, punctuation, spacing and formula
 * delimiters are dropped, and a long probe counts as found when any of its runs
 * appears, so a quote taken from the middle of a paragraph still lands. It stays
 * strict about brevity — a probe too short to be distinctive is answered `false`
 * rather than matched on a coincidence.
 *
 * Pure text handling: no fetching, no React, no i18n.
 */

/** Text reduced to what is worth comparing: the words, without the dressing. */
export function normalizeProbe(text: string): string {
  return text
    .replace(/\$+/g, "")
    .replace(/\\[()[\]]/g, "")
    .replace(/\[\d+\]/g, "")
    .replace(/[，。；：、,.!?;:()（）【】[\]"'“”‘’]/g, "")
    .replace(/\s+/g, "")
    .toLowerCase();
}

/**
 * Whether `probe` appears within `hay`.
 *
 * A probe shorter than `min` has to appear whole — there is not enough of it to
 * sample. A longer one is looked for a run at a time, stepping through it, so a
 * quote that was trimmed at either end (or rejoined across a line break) still
 * counts. Both are normalised by the caller, since what counts as dressing
 * depends on the prose.
 *
 * A probe under 16 characters is refused outright, whatever `min` says.
 */
export function sharedRun(probe: string, hay: string, min = 20): boolean {
  if (probe.length < 16) return false;
  if (probe.length < min) return hay.includes(probe);
  const step = 8;
  for (let i = 0; i <= probe.length - min; i += step) {
    if (hay.includes(probe.slice(i, i + min))) return true;
  }
  return hay.includes(probe.slice(probe.length - min));
}
