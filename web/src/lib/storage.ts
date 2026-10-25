/**
 * The browser's own store, as narrowly as this app uses it.
 *
 * Two things are remembered outside the project: which sources the workbench has
 * seen, and where the reader was. Both take the store as a parameter so they can
 * be exercised in tests, which is why the shape it has to have is named here
 * rather than in each of them.
 *
 * Reaching storage can throw — it is disabled by policy in some browsers, and the
 * object itself is absent when there is no window — so every caller gets null
 * instead and carries on without remembering anything.
 */
export type StorageLike = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  removeItem: (key: string) => void;
};

export function storageOrNull(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
