import { useTranslation } from "react-i18next";
import {
  MarkdownBody,
  MarkdownFlow,
  useMarkdownComponents,
} from "@/components/docs/MarkdownBody";
import { useDocsStore } from "@/stores/docs-store";
import type { PaneSlot } from "@/stores/layout-store";

/**
 * A remark's own text, drawn as the app draws markdown everywhere.
 *
 * A remark is written on one screen and read on another — beside the page it was
 * written about — and the two have to agree: a link that leads somewhere in the
 * preview and nowhere on the page, or a formula that looks different, would make
 * the preview a promise the page does not keep. So both go through the one
 * pipeline, and this only says what a link means while writing.
 */
export function NotePreview({
  body,
  slot = "main",
}: {
  /** The remark's text, as written. */
  body: string;
  /** Where the preview is, so a link it opens lands in the pane next door. */
  slot?: PaneSlot;
}) {
  const { t } = useTranslation();
  const followLink = useDocsStore((state) => state.followLink);
  const hasPage = useDocsStore((state) => state.hasPage);

  const components = useMarkdownComponents({
    onOpenLink: (link: string) => void followLink(link, slot),
    isKnownLink: hasPage,
    missingTitle: t("docs.view.missingPage"),
  });

  return (
    <MarkdownFlow>
      <MarkdownBody text={body} components={components} />
    </MarkdownFlow>
  );
}
