import type { WritingEditable } from "@/stores/writing-store";

/**
 * The pure edits behind the writing-state panel.
 *
 * Every edit returns a new draft and touches only the row it names, so the
 * component only wires an input to one of these and the store only decides
 * whether the draft is dirty. The four areas share the same map/filter shape;
 * keeping it here stops eight near-copies drifting apart.
 */
export function updateSearchHit(
  draft: WritingEditable,
  searchIndex: number,
  hitIndex: number,
  field: "title" | "url" | "snippet",
  value: string,
): WritingEditable {
  return {
    ...draft,
    searches: draft.searches.map((search, index) =>
      index === searchIndex
        ? {
            ...search,
            hits: (search.hits ?? []).map((hit, position) =>
              position === hitIndex
                ? { ...hit, [field]: value }
                : hit,
            ),
          }
        : search,
    ),
  };
}

export function removeSearchHit(
  draft: WritingEditable,
  searchIndex: number,
  hitIndex: number,
): WritingEditable {
  return {
    ...draft,
    searches: draft.searches.map((search, index) =>
      index === searchIndex
        ? {
            ...search,
            hits: (search.hits ?? []).filter(
              (_hit, position) => position !== hitIndex,
            ),
          }
        : search,
    ),
  };
}

export function updatePageTitle(
  draft: WritingEditable,
  index: number,
  value: string,
): WritingEditable {
  return {
    ...draft,
    selectedPages: draft.selectedPages.map((page, position) =>
      position === index ? { ...page, title: value } : page,
    ),
  };
}

export function removePage(
  draft: WritingEditable,
  index: number,
): WritingEditable {
  return {
    ...draft,
    selectedPages: draft.selectedPages.filter(
      (_page, position) => position !== index,
    ),
  };
}

export function updateFrameBlocks(
  draft: WritingEditable,
  index: number,
  value: string,
): WritingEditable {
  return {
    ...draft,
    frames: draft.frames.map((frame, position) =>
      position === index
        ? {
            ...frame,
            blockIds: value
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
          }
        : frame,
    ),
  };
}

export function removeFrame(
  draft: WritingEditable,
  index: number,
): WritingEditable {
  return {
    ...draft,
    frames: draft.frames.filter(
      (_frame, position) => position !== index,
    ),
  };
}

export function updateSection(
  draft: WritingEditable,
  pageIndex: number,
  sectionIndex: number,
  field: "heading" | "body",
  value: string,
): WritingEditable {
  return {
    ...draft,
    draft: {
      pages: draft.draft.pages.map((page, pagePosition) =>
        pagePosition === pageIndex
          ? {
              ...page,
              sections: (page.sections ?? []).map(
                (section, sectionPosition) =>
                  sectionPosition === sectionIndex
                    ? { ...section, [field]: value }
                    : section,
              ),
            }
          : page,
      ),
    },
  };
}

export function removeSection(
  draft: WritingEditable,
  pageIndex: number,
  sectionIndex: number,
): WritingEditable {
  return {
    ...draft,
    draft: {
      pages: draft.draft.pages.map((page, pagePosition) =>
        pagePosition === pageIndex
          ? {
              ...page,
              sections: (page.sections ?? []).filter(
                (_section, sectionPosition) =>
                  sectionPosition !== sectionIndex,
              ),
            }
          : page,
      ),
    },
  };
}
