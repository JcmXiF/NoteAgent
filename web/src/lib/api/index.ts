/**
 * Product API entry point. One module per resource, all sharing the request and
 * SSE helpers in `client.ts`; payload types live in `types.ts`.
 */
export * from "@/lib/api/types";

export * as acquireApi from "@/lib/api/acquire";
export * as chatApi from "@/lib/api/chat";
export * as clipsApi from "@/lib/api/clips";
export * as deepSearchApi from "@/lib/api/deep-search";
export * as filesApi from "@/lib/api/files";
export * as graphApi from "@/lib/api/graph";
export * as inboxApi from "@/lib/api/inbox";
export * as notesApi from "@/lib/api/notes";
export * as planApi from "@/lib/api/plan";
export * as projectsApi from "@/lib/api/projects";

export { ApiError, errorMessage, isSseAborted, postSse, projectPath } from "@/lib/api/client";
