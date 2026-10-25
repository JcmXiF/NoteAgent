import type { ObservationPayload } from "@/lib/api/client";
import { deleteJson, getJson, patchJson, postJson, postSse, putJson } from "@/lib/api/client";
import type {
  ChatConversation,
  ChatLimits,
  ChatLimitsPayload,
  ChatListPayload,
} from "@/lib/api/types";

function chatsPath(project: string, suffix = ""): string {
  return `/projects/${encodeURIComponent(project)}/chats${suffix}`;
}

/**
 * The knobs for one project, with the ranges they may take.
 *
 * The project's own `.noteagent/chat-limits.json` is authoritative, and the ranges
 * come back with the values so nothing here has to know them.
 */
export function fetchChatLimits(project: string): Promise<ChatLimitsPayload> {
  return getJson<ChatLimitsPayload>(`/projects/${encodeURIComponent(project)}/chat-limits`);
}

export function putChatLimits(project: string, body: ChatLimits): Promise<ChatLimitsPayload> {
  return putJson<ChatLimitsPayload>(
    `/projects/${encodeURIComponent(project)}/chat-limits`,
    body,
  );
}

export function fetchChats(project: string): Promise<ChatListPayload> {
  return getJson<ChatListPayload>(chatsPath(project));
}

export function createChat(project: string, title = ""): Promise<ChatConversation> {
  return postJson<ChatConversation>(chatsPath(project), { title });
}

export function fetchChat(
  project: string,
  conversationId: string,
): Promise<ChatConversation> {
  return getJson<ChatConversation>(
    chatsPath(project, `/${encodeURIComponent(conversationId)}`),
  );
}

/**
 * Gives a conversation a name of the reader's own.
 *
 * An empty name is not a refusal: it means "not named", which puts back the name the
 * conversation takes from its first question.
 */
export function renameChat(
  project: string,
  conversationId: string,
  title: string,
): Promise<ChatConversation> {
  return patchJson<ChatConversation>(
    chatsPath(project, `/${encodeURIComponent(conversationId)}`),
    { title },
  );
}

export function deleteChat(
  project: string,
  conversationId: string,
): Promise<ChatListPayload> {
  return deleteJson<ChatListPayload>(
    chatsPath(project, `/${encodeURIComponent(conversationId)}`),
  );
}

/**
 * Asks a question and streams the answer. `status` names the current phase
 * (think / tool / answer) and `token` carries the text as it arrives.
 */
export function queryChat(
  project: string,
  conversationId: string,
  content: string,
  options: {
    onToken?: (text: string) => void;
    /** A round that was not writing the answer takes back what it sent. */
    onTokenReset?: () => void;
    /** Something the assistant looked at, as data rather than prose. */
    onObservation?: (observation: ObservationPayload) => void;
    onStatus?: (message: string, stepId?: string) => void;
    signal?: AbortSignal;
  } = {},
): Promise<ChatConversation> {
  return postSse<ChatConversation>(
    chatsPath(project, `/${encodeURIComponent(conversationId)}/query`),
    { content },
    {
      onToken: options.onToken,
      onTokenReset: options.onTokenReset,
      onObservation: options.onObservation,
      onStatus: options.onStatus,
      signal: options.signal,
    },
  );
}
