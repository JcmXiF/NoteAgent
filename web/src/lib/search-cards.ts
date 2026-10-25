import { MAX_FETCH_CARDS } from "@/lib/api/acquire";
import type { ResourceCard } from "@/lib/api/types";

/**
 * Which search results are ticked, and what ticking one more means.
 *
 * The backend reads a limited number of links per request, so the list of ticked
 * cards is capped here rather than being refused after it has been sent. Ticking is
 * the reader's decision; saying that the limit is reached is part of answering it.
 */

type ToggleResult = {
  selected: string[];
  /** Set when the tick could not be given, so the reader can be told why. */
  refused: boolean;
};

/**
 * Ticks or unticks one result.
 *
 * A card already ticked can always be unticked, even while the limit is reached:
 * otherwise the limit would be a place the reader cannot get out of.
 */
export function toggleSelected(
  selected: string[],
  url: string,
  max = MAX_FETCH_CARDS,
): ToggleResult {
  if (selected.includes(url)) {
    return { selected: selected.filter((item) => item !== url), refused: false };
  }
  if (selected.length >= max) return { selected, refused: true };
  return { selected: [...selected, url], refused: false };
}

/** The cards that are ticked, in the order they were found. */
export function selectedCards(cards: ResourceCard[], selected: string[]): ResourceCard[] {
  const wanted = new Set(selected);
  return cards.filter((card) => wanted.has(card.url));
}

/** Whether the limit has been reached, for saying so before a tick is refused. */
export function atLimit(selected: string[], max = MAX_FETCH_CARDS): boolean {
  return selected.length >= max;
}

/**
 * Drops ticks for cards that are no longer in the list.
 *
 * A new search replaces the results, and a tick for a card that is gone would be a
 * request for a page the reader can no longer see.
 */
export function keepKnown(selected: string[], cards: ResourceCard[]): string[] {
  const known = new Set(cards.map((card) => card.url));
  const kept = selected.filter((url) => known.has(url));
  return kept.length === selected.length ? selected : kept;
}
