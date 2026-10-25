import { useEffect, useRef } from "react";

/**
 * Calls `load` on an interval, but only while it is worth doing.
 *
 * Two things make the difference between helpful and wasteful: the panel being
 * open, and the page being visible. A background tab polling an endpoint every
 * few seconds costs the server for nothing, and a panel nobody is looking at
 * does not need to be current.
 *
 * It also runs once when it becomes enabled, so reopening a panel does not show
 * stale contents while waiting for the first tick.
 */
export function usePoll(
  load: () => void | Promise<void>,
  enabled: boolean,
  intervalMs = 8000,
): void {
  // The callback changes every render in most callers; holding it in a ref keeps
  // the interval from being torn down and rebuilt each time.
  const latest = useRef(load);
  latest.current = load;

  useEffect(() => {
    if (!enabled) return undefined;
    let timer: number | undefined;

    const tick = () => {
      if (document.visibilityState === "visible") void latest.current();
      timer = window.setTimeout(tick, intervalMs);
    };

    void latest.current();
    timer = window.setTimeout(tick, intervalMs);

    const onVisible = () => {
      // Coming back to the tab should not wait out the interval.
      if (document.visibilityState === "visible") void latest.current();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, intervalMs]);
}
