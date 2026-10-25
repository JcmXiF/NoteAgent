import { useTranslation } from "react-i18next";
import { BlockSelection } from "@/components/source/BlockSelection";
import { fileBytesUrl } from "@/lib/api/files";
import { imageDataUrl } from "@/lib/capture";
import type { DerivedBlock } from "@/lib/api/types";

/**
 * Picks an image whole.
 *
 * An image is one block (`img`), so there is nothing to choose between — the
 * point of the list is that adding it to a selection goes through the same path
 * as any other source. Selecting a region within the image is not supported yet.
 */
export function ImagePicker({
  blocks,
  imageBase64,
  imageMediaType,
  project,
  path,
  sourceLabel,
  alt,
  onCommit,
}: {
  blocks: DerivedBlock[];
  /** Present while the image is still a preview rather than a stored file. */
  imageBase64?: string;
  imageMediaType?: string;
  project?: string;
  /** Set once the original has been stored, so the bytes come from the project. */
  path?: string;
  sourceLabel: string;
  alt?: string;
  onCommit: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const src = imageBase64
    ? imageDataUrl(imageBase64, imageMediaType)
    : project && path
      ? fileBytesUrl(project, path)
      : "";

  return (
    <BlockSelection blocks={blocks} onCommit={onCommit}>
      <div className="flex flex-col gap-2 p-2">
        {src ? (
          <img
            className="mx-auto max-h-[60vh] max-w-full bg-white object-contain shadow-sm"
            src={src}
            alt={alt || sourceLabel}
          />
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {t("sources.picker.noOriginal")}
          </p>
        )}
      </div>
    </BlockSelection>
  );
}
