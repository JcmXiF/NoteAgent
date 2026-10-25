import { getJson, projectPath } from "@/lib/api/client";
import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";

export async function fetchGraph(project: string): Promise<{
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
}> {
  const payload = await getJson<{
    project: string;
    nodes: WikiGraphNode[];
    edges: WikiGraphEdge[];
  }>(projectPath(project, "graph"));
  return { nodes: payload.nodes ?? [], edges: payload.edges ?? [] };
}
