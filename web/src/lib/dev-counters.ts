/**
 * Counts of how often something was rendered, for checking a claim in a browser.
 *
 * A view that animates by writing to the DOM should not be re-rendering React for
 * every frame, and the only way to be sure of that is to count. This is read from
 * the console through the development bridge.
 *
 * Development only: in a production build the flag at the call site is a literal
 * false, so neither the calls nor this module reach the bundle.
 */

const counts = new Map<string, number>();

export function countRender(name: string): void {
  counts.set(name, (counts.get(name) ?? 0) + 1);
}

export function renderCounts(): Record<string, number> {
  return Object.fromEntries(counts);
}

export function resetRenderCounts(): void {
  counts.clear();
}
