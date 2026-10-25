import { getJson, postJson } from "@/lib/api/client";

/**
 * What was removed and can still be put back.
 *
 * Every removal keeps the bytes beside the projects for thirty days. The
 * interface never decides how long that is: the core does, and this only says
 * what it kept.
 */
export type TrashKind = "project" | "wiki" | "file";

export type TrashItem = {
  id: string;
  kind: TrashKind | "";
  /** The project's name, or the file's name, or the project a wiki belonged to. */
  name: string;
  project: string;
  relativePath: string;
  removedAt: string;
  removedBy: string;
  fileCount: number;
};

export async function fetchTrash(): Promise<TrashItem[]> {
  const payload = await getJson<{ items: TrashItem[] }>("/trash");
  return payload.items ?? [];
}

export function restoreTrash(
  id: string,
): Promise<{ restored: TrashItem; replaced: string | null }> {
  return postJson<{ restored: TrashItem; replaced: string | null }>(
    `/trash/${encodeURIComponent(id)}/restore`,
  );
}
