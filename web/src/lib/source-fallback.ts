import { isHttpUrl, isLocalCaptureUrl } from "@/lib/capture";

/**
 * The ways back to a source whose text is not here.
 *
 * A source's text is read from one of two places: the capture this session made, or
 * the original stored under `raw/`. When neither can be shown, what is left
 * is how to get the text back — and that differs by where the source came from. A
 * page can be asked for again; one that only opens after signing in can be opened in
 * a browser that can sign in; a file the reader brought in has no address to ask
 * for, so adding the file again is the way, and it is enough because the same bytes
 * are the same source.
 *
 * Which of these apply decides whether a reader is stuck, so it is decided here
 * rather than inside the view, where it could only be checked by hand.
 */
export type SourceFallback = {
  /** The address to ask for again, when a request can bring the text back. */
  refetchUrl: string | null;
  /** The address to open in a browser, when signing in is what it takes. */
  browserUrl: string | null;
  /** Whether the file has to be added again, having no address of its own. */
  readdFile: boolean;
};

const NOTHING: SourceFallback = {
  refetchUrl: null,
  browserUrl: null,
  readdFile: false,
};

export function fallbackFor(input: {
  /** Where the source came from, or the identity it was given in its place. */
  address: string;
  /** Whether a browser is already showing that page. */
  inBrowser?: boolean;
}): SourceFallback {
  const address = (input.address ?? "").trim();
  if (isHttpUrl(address)) {
    return {
      refetchUrl: address,
      // A page the browser is already on is taken from it, which keeps the session
      // it was signed in for; opening it again would start that over.
      browserUrl: input.inBrowser ? null : address,
      readdFile: false,
    };
  }
  if (isLocalCaptureUrl(address)) return { ...NOTHING, readdFile: true };
  return NOTHING;
}
