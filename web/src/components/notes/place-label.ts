import type { NotePlace } from "@/lib/note-list";
import type { TFunction } from "i18next";

/**
 * Where a remark sits, in the reader's words.
 *
 * A section is named by its own heading rather than by a word for "section",
 * because the heading is what the remark was placed against and what the reader
 * would look for.
 */
export function placeLabel(place: NotePlace, t: TFunction): string {
  switch (place.kind) {
    case "preamble":
      return t("remark.place.preamble");
    case "end":
      return t("remark.place.end");
    case "section":
      return place.heading;
    case "none":
      return t("remark.place.unplaced");
  }
}
