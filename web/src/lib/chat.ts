import type { SessionTurn } from "@/lib/api/types";

/**
 * Whether every word typed appears in the text, in any order.
 *
 * The one matching rule the interface uses for its own lists, so the session
 * list and the command palette do not disagree about what "kl ent" means.
 */
export function matchesWords(text: string, query: string): boolean {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = text.toLocaleLowerCase();
  return words.every((word) => haystack.includes(word));
}

/**
 * Whether a session answers to what was typed in the list's search box.
 *
 * An unnamed session is found by the words the list shows it under, so what is
 * on screen and what is searched are the same thing.
 */
export function matchesConversation(
  title: string,
  untitled: string,
  query: string,
): boolean {
  return matchesWords(title.trim() || untitled, query);
}

/** The part of a transcript turn this module reads. */
type SpokenTurn = Pick<SessionTurn, "role" | "content">;

/**
 * The question an answer is answering.
 *
 * A remark saved from an answer quotes the question it came from, and a question
 * is found by reading back to the nearest one — a turn that failed leaves its
 * question with no answer after it, and the next answer is still the answer to the
 * question before it.
 */
export function questionBefore(
  messages: SpokenTurn[],
  index: number,
): string | null {
  for (let at = index - 1; at >= 0; at -= 1) {
    const turn = messages[at];
    if (turn && turn.role === "user") return turn.content;
  }
  return null;
}
