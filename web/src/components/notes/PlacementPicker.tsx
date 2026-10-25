import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { NoteFields } from "@/lib/api/notes";
import { placements, type Placement } from "@/lib/note-anchor";
import { cn } from "@/lib/utils";

/**
 * Where the remark goes on the page.
 *
 * The places offered are the page's own — its opening, each of its sections, its
 * end — read from the page itself rather than from a list this screen keeps, so a
 * remark can only be put somewhere the page has. Picking a section also chooses
 * what to quote from it, which is the way back to that place if the heading is
 * later renamed.
 */
export function PlacementPicker({
  body,
  anchor,
  onPick,
}: {
  /** The page's prose; empty when it could not be read. */
  body: string;
  /** The anchor the remark holds now. */
  anchor: string;
  onPick: (place: Pick<NoteFields, "anchor" | "quote">) => void;
}) {
  const { t } = useTranslation();
  const options = useMemo(() => placements(body), [body]);
  const known = options.some((place) => place.anchor === anchor);

  return (
    <section className="flex flex-col gap-1">
      <h3 className="text-[11px] font-medium text-muted-foreground">
        {t("remark.place.label")}
      </h3>
      <ul className="flex max-h-56 flex-col overflow-y-auto scroll-thin rounded-lg border">
        {options.map((place) => (
          <li key={`${place.kind}-${place.anchor}`}>
            <PlaceRow
              place={place}
              active={place.anchor === anchor}
              onPick={() => onPick({ anchor: place.anchor, quote: place.quote })}
            />
          </li>
        ))}
      </ul>
      {!known ? (
        <p className="text-[11px] text-warning">{t("remark.place.stale")}</p>
      ) : null}
      {body ? null : (
        <p className="text-[11px] text-muted-foreground">{t("remark.place.unreadable")}</p>
      )}
    </section>
  );
}

function PlaceRow({
  place,
  active,
  onPick,
}: {
  place: Placement;
  active: boolean;
  onPick: () => void;
}) {
  const { t } = useTranslation();
  const label =
    place.kind === "section"
      ? (place.heading ?? "")
      : place.kind === "preamble"
        ? t("remark.place.preamble")
        : place.kind === "end"
          ? t("remark.place.end")
          : t("remark.place.none");
  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={active}
      // A section is set in by its depth, so the list reads in the page's shape.
      style={{ paddingInlineStart: `${8 + ((place.level ?? 2) - 2) * 12}px` }}
      className={cn(
        "block w-full truncate py-1 pr-2 text-left text-[11px]",
        active ? "bg-accent font-medium" : "hover:bg-accent/40",
      )}
    >
      {label}
    </button>
  );
}
