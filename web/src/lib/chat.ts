import type { ChatTurn } from "@/lib/api/types";

/**
 * A turn being asked and answered.
 *
 * The question is kept here rather than in the conversation's messages until the
 * core confirms it has it, so a turn that fails leaves nothing behind and the
 * transcript on screen is always one the project actually holds.
 */
export type PendingTurn = {
  question: string;
  /** The answer as it is written so far. */
  answer: string;
  /**
   * How far along the turn is: the furthest of `think`, `tool` and `answer` that
   * the core has named. It only ever moves forward, because a turn that goes back
   * to thinking after using a tool has not gone backwards.
   */
  step: string | null;
};

/** The turn's phases, in the order they happen. */
const STEP_ORDER = ["think", "tool", "answer"];

/**
 * How far along a turn is, given the phase the core just named.
 *
 * A turn revisits `think` after every tool call, and a track that followed that
 * literally would appear to rewind. A phase this side does not know leaves the
 * answer alone, which keeps a new phase from jumping the track to the end.
 */
export function furthestStep(current: string | null, named: string | null): string | null {
  const rank = (step: string | null) => (step ? STEP_ORDER.indexOf(step) : -1);
  const next = rank(named);
  if (next < 0) return current;
  return next > rank(current) ? named : current;
}

/**
 * What to say while a turn is being worked on.
 *
 * The core reports progress in wording written for whoever reads the log, so the
 * screen is given its own sentence, chosen by the step the core named. A step this
 * side does not know is read as still thinking, which is true of every step before
 * an answer is written.
 */
export function activityKey(step: string | null): string {
  return step === "tool" || step === "answer"
    ? `chat.activity.${step}`
    : "chat.activity.think";
}

/**
 * The phase to describe right now.
 *
 * The core only names the answering phase when it is about to save, which is after
 * the answer has been written, so a turn would spend the whole time it is writing
 * saying it is still looking things up. Text arriving is the answer being written,
 * and that is worth saying at the moment it happens.
 */
export function spokenStep(answer: string, step: string | null): string | null {
  return answer.trim() ? "answer" : step;
}

/** Whether the composer has something it can send right now. */
export function canSend(text: string, maxChars: number | null, busy: boolean): boolean {
  if (busy) return false;
  const question = text.trim();
  if (!question) return false;
  // With no limit known, the length is not this side's to judge: the core is the one
  // that knows, and its answer is what the reader sees.
  if (maxChars === null) return true;
  return question.length <= maxChars;
}

/** How close to the end still counts as reading along, in pixels. */
const FOLLOW_MARGIN = 48;

/**
 * Whether a scrolled area is at its end.
 *
 * An answer being written should keep the reader at the end of it, but a reader
 * who scrolled up to read something earlier should not be dragged back down, so
 * the two cases are told apart by where they are rather than by whether text is
 * still arriving.
 */
export function isAtEnd(view: {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}): boolean {
  const below = view.scrollHeight - view.scrollTop - view.clientHeight;
  if (!Number.isFinite(below)) return true;
  return below <= FOLLOW_MARGIN;
}

/**
 * Whether a question that failed is still only in the composer's hands.
 *
 * The core saves a question before it starts answering, so a failed turn may
 * already be part of the conversation. Putting it back in the composer in that
 * case would ask it twice.
 */
export function questionIsUnsent(messages: ChatTurn[], question: string): boolean {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "user") return true;
  return last.content.trim() !== question.trim();
}

/**
 * The question an answer is answering.
 *
 * A remark saved from an answer quotes the question it came from, and a question
 * is found by reading back to the nearest one — a turn that failed leaves its
 * question with no answer after it, and the next answer is still the answer to the
 * question before it.
 */
export function questionBefore(messages: ChatTurn[], index: number): string | null {
  for (let at = index - 1; at >= 0; at -= 1) {
    const turn = messages[at];
    if (turn && turn.role === "user") return turn.content;
  }
  return null;
}

/**
 * Whether every word typed appears in the text, in any order.
 *
 * The one matching rule the interface uses for its own lists, so the conversation
 * list and the command palette do not disagree about what "kl ent" means.
 */
export function matchesWords(text: string, query: string): boolean {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = text.toLocaleLowerCase();
  return words.every((word) => haystack.includes(word));
}

/**
 * Whether a conversation answers to what was typed in the list's search box.
 *
 * An unnamed conversation is found by the words the list shows it under, so what is
 * on screen and what is searched are the same thing.
 */
export function matchesConversation(
  title: string,
  untitled: string,
  query: string,
): boolean {
  return matchesWords(title.trim() || untitled, query);
}
