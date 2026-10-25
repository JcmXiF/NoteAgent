import { postJson, projectPath } from "@/lib/api/client";
import type { CapturePreview, RawSaveItem, ResourceCard } from "@/lib/api/types";

/** Extra call options shared by the acquisition requests. */
type RequestOptions = {
  /** Aborting it stops waiting; the backend stops before its next model call. */
  signal?: AbortSignal;
};

/** Up to 20 result cards, matching the backend's `top_k` ceiling. */
export function postAcquireSearch(
  project: string,
  query: string,
  topK?: number,
  options: RequestOptions = {},
): Promise<{ query: string; provider: string; cards: ResourceCard[] }> {
  return postJson<{ query: string; provider: string; cards: ResourceCard[] }>(
    projectPath(project, "acquire/search"),
    { query, top_k: topK },
    { signal: options.signal },
  );
}

/** Up to `MAX_FETCH_CARDS` URLs, matching the backend's `max_length`. */
export function postAcquireFetch(
  project: string,
  urls: string[],
  query?: string,
  options: RequestOptions = {},
): Promise<{ query: string; captures: CapturePreview[] }> {
  return postJson<{ query: string; captures: CapturePreview[] }>(
    projectPath(project, "acquire/fetch"),
    { urls, query: query ?? "" },
    { signal: options.signal },
  );
}

/** HTML rendered in the guest browser, kept on the shell side so cookies never reach Python. */
export function postAcquireFromRendered(
  project: string,
  url: string,
  html: string,
  options: RequestOptions = {},
): Promise<{ url: string; captures: CapturePreview[] }> {
  return postJson<{ url: string; captures: CapturePreview[] }>(
    projectPath(project, "acquire/from-rendered"),
    { url, html },
    { signal: options.signal },
  );
}

export type LocalFilePayload = {
  name: string;
  mediaType: string;
  contentBase64: string;
};

export function postAcquireFromLocal(
  project: string,
  files: LocalFilePayload[],
  options: RequestOptions = {},
): Promise<{ captures: CapturePreview[] }> {
  return postJson<{ captures: CapturePreview[] }>(
    projectPath(project, "acquire/from-local"),
    { files },
    { signal: options.signal },
  );
}

/**
 * Stores the original files behind the given captures. Compilation calls the
 * same path, so material is on disk before anything is written to the wiki.
 */
export function postAcquireSave(
  project: string,
  captures: CapturePreview[],
): Promise<{ written: string[]; items: RawSaveItem[] }> {
  return postJson<{ written: string[]; items: RawSaveItem[] }>(
    projectPath(project, "acquire/save"),
    {
      captures: captures.map((capture) => ({
        url: capture.url,
        markdown: capture.markdown,
        html: capture.html ?? "",
        title: capture.title,
        captureMethod: capture.captureMethod,
        fidelity: capture.fidelity,
        reasons: capture.reasons,
        httpStatus: capture.httpStatus,
        finalUrl: capture.finalUrl,
        charCount: capture.charCount,
        imageCount: capture.imageCount ?? 0,
        imagesFailed: capture.imagesFailed ?? 0,
        kind: capture.kind ?? "",
        pdfBase64: capture.pdfBase64 ?? "",
        officeBase64: capture.officeBase64 ?? "",
        imageBase64: capture.imageBase64 ?? "",
        pageCount: capture.pageCount ?? 0,
      })),
    },
  );
}

/** Kept in step with the backend request limits so the UI can disable buttons. */
export const MAX_FETCH_CARDS = 8;
export const MAX_LOCAL_FILES = 4;
/** The backend rejects a larger payload, so the check happens here first. */
export const MAX_LOCAL_BYTES = 20 * 1024 * 1024;
