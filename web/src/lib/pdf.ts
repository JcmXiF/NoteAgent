import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";

/**
 * PDF rendering support.
 *
 * The worker is configured once here rather than inside a component, so the
 * side effect happens a single time and cannot be missed by a view that forgets
 * to set it.
 */
GlobalWorkerOptions.workerSrc = pdfWorker;

export { getDocument };
export type { PDFDocumentProxy };

/** Bytes from a base64 payload, which is how unsaved captures carry a PDF. */
export function bytesFromBase64(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** The page number a block id refers to, for ids like `p12` and `p12.b3`. */
export function pageFromBlockId(id: string): number | null {
  const match = /^p(\d+)/i.exec(id);
  if (!match) return null;
  const page = Number(match[1]);
  return Number.isFinite(page) && page > 0 ? page : null;
}
