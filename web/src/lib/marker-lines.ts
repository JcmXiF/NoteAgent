/**
 * How a stamped marker line is read.
 *
 * `na-clip` and `na-ecg` are the two anchors the pipeline writes into a body.
 * Both are HTML comments standing on a line of their own, and both own the
 * heading that follows them. Only an `na-clip` line moves a section boundary, so
 * its pattern is kept here, next to its Python counterpart in
 * `src/noteagent/ingest/marker_lines.py`: the two readers cannot drift apart as
 * more markers appear.
 *
 * The rule, in full:
 *
 * - A marker occupies its own line; leading and trailing spaces are fine.
 * - It owns the next line that has content in it. Blank lines may separate the
 *   two; nothing else may.
 * - That line has to be a section heading (`##` through `######`). `# Title` is
 *   the page's own name, not a section, so it never owns a marker.
 * - A marker with no heading under it owns nothing. It is left on disk untouched
 *   and reported as unowned; stepping on to a later heading would attribute a
 *   section to material it was not written from.
 */

/** One `na-clip` marker and nothing else on the line. */
export const CLIP_MARK_LINE =
  /^[ \t]*<!--\s*na-clip:([^\s>]+)(?:\s+([bB]\d+(?:\s*,\s*[bB]\d+)*))?\s*-->[ \t]*$/i;

/** `## Heading` through `###### Heading`, with a name on it. */
export const HEADING_LINE = /^(#{2,6})[ \t]+(.+?)\s*$/;
