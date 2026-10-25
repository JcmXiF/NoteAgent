import { projectExportUrl } from "@/lib/api/projects";

/**
 * Hands the project's archive to the browser to save.
 *
 * The core builds the zip; all this does is point the browser at it. A hidden link
 * is used rather than `window.open` because a download should not take the window
 * away from the reader — the project stays on screen while the file arrives.
 */
export function downloadProjectArchive(project: string): void {
  const anchor = document.createElement("a");
  anchor.href = projectExportUrl(project);
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
