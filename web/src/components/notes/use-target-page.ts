import { useEffect, useMemo } from "react";
import { pageBody } from "@/lib/page-body";
import { readSections, type SectionedBody } from "@/lib/sections";
import { useDocsReads } from "@/stores/docs-reads";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The page the remarks are being written about, read as the reading surface reads
 * it.
 *
 * A remark names a heading, and a heading only means something next to the page
 * that has it, so writing a remark needs the page's text. It is read through the
 * same cache the reading surface uses rather than fetched again: the page the
 * reader has open is already in hand, and reading it twice would let the two
 * disagree about the sections.
 */

const EMPTY: SectionedBody = { preamble: { start: 0, end: 0 }, sections: [] };

type TargetPage = {
  /** Whether the page's text is in hand. */
  status: "none" | "loading" | "ready" | "error";
  /** The page's prose, empty unless the text is in hand. */
  body: string;
  /** The page's sections, as the reading surface would find them. */
  split: SectionedBody;
  error: string | null;
};

export function useTargetPage(target: string | null): TargetPage {
  const project = useWorkspaceStore((state) => state.current);
  const read = useDocsReads((state) => (target ? state.reads[target] : undefined));
  const load = useDocsReads((state) => state.read);

  useEffect(() => {
    if (project && target) void load(project, target);
  }, [project, target, load]);

  // A page is prose only if it is a note; a stored original is a different kind of
  // document even when it reads as markdown, and remarks are not written about it.
  const markdown = read?.kind ? read.kind === "markdown" : Boolean(target?.endsWith(".md"));
  const body = useMemo(
    () => (read?.status === "ready" && markdown ? pageBody(read.content) : ""),
    [read?.status, read?.content, markdown],
  );
  const split = useMemo(() => (body ? readSections(body) : EMPTY), [body]);

  if (!target) return { status: "none", body: "", split: EMPTY, error: null };
  if (!read || read.status === "loading") {
    return { status: "loading", body: "", split: EMPTY, error: null };
  }
  if (read.status === "error") {
    return { status: "error", body: "", split: EMPTY, error: read.error };
  }
  if (!markdown) return { status: "error", body: "", split: EMPTY, error: null };
  return { status: "ready", body, split, error: null };
}
