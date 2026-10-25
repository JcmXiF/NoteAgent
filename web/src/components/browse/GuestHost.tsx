import { useLayoutEffect, useRef } from "react";
import { boundsFor, hiddenBounds, sameBounds } from "@/lib/browse";
import { desktopBridge, type GuestBounds } from "@/lib/desktop";
import { useBrowseStore } from "@/stores/browse-store";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * The one place that tells the shell where to put its page.
 *
 * The browser is one surface, but either pane can show it, so the position has to
 * be decided once rather than by each view that renders the module: two panes both
 * reporting — one for itself, one taking the page away — would leave the page
 * wherever the slower of them put it. A pane claims the area it has made, and this
 * measures what has been claimed.
 *
 * It renders nothing. It is mounted by the shell so that it outlives every module,
 * and so that leaving the module is a change of claim rather than the page being
 * abandoned where it stood.
 */
export function GuestHost() {
  const claimed = useBrowseStore((state) => state.claimed);
  // Something in front of the page — a dialog, the jobs list — takes the page away
  // while it is there, because the shell draws the page *over* the page.
  const held = useLayoutStore((state) => state.guestHolds > 0);
  const element = claimed?.element ?? null;
  const last = useRef<GuestBounds>(hiddenBounds());

  useLayoutEffect(() => {
    const bridge = desktopBridge();
    const setBounds = bridge?.setGuestBounds;
    if (!setBounds) return undefined;

    let raf = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const report = () => {
      const next = element && !held ? boundsFor(element.getBoundingClientRect()) : hiddenBounds();
      if (sameBounds(last.current, next)) return;
      last.current = next;
      void setBounds(next);
    };
    const run = () => {
      raf = 0;
      if (timer !== undefined) {
        clearTimeout(timer);
        timer = undefined;
      }
      report();
    };
    const schedule = () => {
      if (raf || timer !== undefined) return;
      if (document.visibilityState === "visible") {
        // A frame, so a burst of layout changes is measured once, after layout.
        raf = window.requestAnimationFrame(run);
      } else {
        // A hidden window runs no animation frames, and the page still has to be
        // told where it goes: it is placed when the module opens and taken away when
        // the module is left, whether or not anyone is looking at the window.
        timer = setTimeout(run, 0);
      }
    };

    // Once now, so the first placement never waits for a frame that may not come.
    report();
    const observer = new ResizeObserver(schedule);
    if (element) observer.observe(element);
    const stopFrame = bridge.onFrame?.(schedule);
    window.addEventListener("resize", schedule);

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      if (timer !== undefined) clearTimeout(timer);
      observer.disconnect();
      stopFrame?.();
      window.removeEventListener("resize", schedule);
      // Nothing has claimed the area, so the page has nowhere to be. When the claim
      // has merely moved to the other pane it is not taken away: it would blink out
      // and back for no reason the reader could see.
      if (useBrowseStore.getState().claimed) return;
      last.current = hiddenBounds();
      void setBounds(hiddenBounds());
    };
  }, [element, held]);

  return null;
}
