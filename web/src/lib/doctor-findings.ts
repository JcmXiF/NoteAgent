import type { DoctorFinding } from "@/lib/api/types";

/**
 * How the check's findings are said to the reader.
 *
 * A finding arrives as a stable code, the figures that code means, and a sentence
 * the core wrote for whoever reads a log — in one language, decided there. What the
 * reader sees is worded here, in their language, from the code and its figures, so
 * nothing on screen is prose written for a log.
 *
 * A code this version has never heard of says that something needs a look rather
 * than printing the core's words: the figures are not a sentence, and a sentence in
 * the wrong language is worse than a vague one.
 */

/** The wording function, as the components already have it. */
type T = (key: string, options?: Record<string, unknown>) => string;

/**
 * The codes that can repeat for one page, and the figure that tells the repeats
 * apart. `null` is a code with nothing to list — several of it is a count.
 *
 * A code absent from this table is one line per finding, because a page can have at
 * most one of it.
 */
const REPEATS: Record<string, string | null> = {
  "link.broken": "target",
  "index.ghost": "slug",
  "source.missing": "locator",
  "mark.clip_bad": null,
};

/** How many figures a merged line names before it counts the rest. */
const LISTED_LIMIT = 5;

/**
 * One line of the list: a finding, or several findings of one code about one page.
 *
 * `info` never becomes a line. An orphan page and an overview left by an older
 * project are what a healthy, unmigrated project looks like, so listing them beside
 * what is actually wrong would teach the reader to ignore the list.
 */
type FindingLine = {
  code: string;
  severity: string;
  path: string | null;
  /** How many findings this line stands for. */
  count: number;
  /** The figures of the first of them, for the wording of a single finding. */
  params: Record<string, string>;
  /** The figures that differ between them, in the order found. */
  items: string[];
};

export function findingLines(findings: DoctorFinding[]): FindingLine[] {
  const lines: FindingLine[] = [];
  const groups = new Map<string, Group>();

  for (const finding of findings) {
    if (finding.severity === "info") continue;
    const params = finding.params ?? {};
    const figure = finding.code in REPEATS ? REPEATS[finding.code] : undefined;
    if (figure === undefined) {
      lines.push(lineFor(finding, params));
      continue;
    }
    const key = `${finding.code}\u0000${finding.path ?? ""}`;
    let group = groups.get(key);
    if (!group) {
      group = { line: lineFor(finding, params), seen: 0 };
      groups.set(key, group);
      lines.push(group.line);
    }
    group.seen += 1;
    const item = figure ? params[figure] ?? "" : "";
    if (item && !group.line.items.includes(item)) group.line.items.push(item);
  }

  for (const group of groups.values()) {
    // What the reader acts on is what is named, not how often it is named: a page
    // linking three times to the same missing page is one page to write. A code
    // with nothing to name stands for as many findings as there were.
    group.line.count =
      group.line.items.length > 0 ? group.line.items.length : group.seen;
  }
  return lines;
}

type Group = { line: FindingLine; seen: number };

function lineFor(
  finding: DoctorFinding,
  params: Record<string, string>,
): FindingLine {
  return {
    code: finding.code,
    severity: finding.severity,
    path: finding.path,
    count: 1,
    params,
    items: [],
  };
}

/** The reader's word for how serious a line is. */
export function severityWord(severity: string, t: T): string {
  // An unheard-of severity is read as the milder one: better to understate a
  // problem than to shout about something nobody has seen before.
  return t(`compile.severity.${severity === "error" ? "error" : "warning"}`);
}

/** The line as the reader reads it. */
export function findingSentence(line: FindingLine, t: T): string {
  const args =
    line.count > 1
      ? { count: String(line.count), items: listed(line, t) }
      : { ...line.params };
  const keys = [
    ...(line.count > 1 ? [`compile.finding.${line.code}.many`] : []),
    `compile.finding.${line.code}`,
    "compile.finding.unknown",
  ];
  for (const key of keys) {
    const text = t(key, { ...args, defaultValue: "" });
    if (text) return text;
  }
  return "";
}

/** The figures a merged line names, with the count of whatever did not fit. */
function listed(line: FindingLine, t: T): string {
  const names = line.items
    .slice(0, LISTED_LIMIT)
    .join(t("common.listSeparator"));
  const rest = line.items.length - LISTED_LIMIT;
  return rest > 0 ? names + t("compile.finding.more", { count: rest }) : names;
}
