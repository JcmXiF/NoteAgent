import type { ChatLimitBounds, ChatLimits, ChatLimitsPayload } from "@/lib/api/types";

/**
 * How the chat's knobs are grouped, named and measured.
 *
 * The list is the single place that knows how the twelve knobs are grouped and what
 * each one is called, so the panel is a rendering of this rather than twelve copies
 * of the same markup. The order here is the order on screen.
 *
 * What each knob may be set to is **not** here: the core decides that, and sends the
 * ranges with the values. Keeping a second copy would be a copy that drifts, and
 * then the panel would block a value the core accepts or offer one it refuses.
 */
type LimitField = {
  key: keyof ChatLimits;
  /** i18n key for the field's name. */
  label: string;
  /** i18n key for the sentence under it, when the effect needs explaining. */
  hint?: string;
  /** i18n key for the unit shown after the input. */
  unit: string;
};

type LimitGroup = {
  /** i18n key for the group's heading. */
  title: string;
  fields: LimitField[];
};

export const CHAT_LIMIT_GROUPS: LimitGroup[] = [
  {
    title: "chat.limits.group.conversation",
    fields: [
      {
        key: "historyLimit",
        label: "chat.limits.historyLimit",
        hint: "chat.limits.historyLimitHint",
        unit: "chat.limits.unit.messages",
      },
      {
        key: "maxMessages",
        label: "chat.limits.maxMessages",
        unit: "chat.limits.unit.messages",
      },
      {
        key: "maxContentChars",
        label: "chat.limits.maxContentChars",
        unit: "chat.limits.unit.chars",
      },
      {
        key: "maxTitleChars",
        label: "chat.limits.maxTitleChars",
        unit: "chat.limits.unit.chars",
      },
      {
        key: "maxConversations",
        label: "chat.limits.maxConversations",
        unit: "chat.limits.unit.conversations",
      },
    ],
  },
  {
    title: "chat.limits.group.context",
    fields: [
      {
        key: "maxBriefChars",
        label: "chat.limits.maxBriefChars",
        hint: "chat.limits.maxBriefCharsHint",
        unit: "chat.limits.unit.chars",
      },
      {
        key: "maxIndexChars",
        label: "chat.limits.maxIndexChars",
        hint: "chat.limits.contextHint",
        unit: "chat.limits.unit.chars",
      },
    ],
  },
  {
    title: "chat.limits.group.reading",
    fields: [
      {
        key: "maxOpenPages",
        label: "chat.limits.maxOpenPages",
        unit: "chat.limits.unit.pages",
      },
      {
        key: "maxPageChars",
        label: "chat.limits.maxPageChars",
        unit: "chat.limits.unit.chars",
      },
      {
        key: "maxPagesChars",
        label: "chat.limits.maxPagesChars",
        hint: "chat.limits.maxPagesCharsHint",
        unit: "chat.limits.unit.chars",
      },
      {
        key: "maxToolRounds",
        label: "chat.limits.maxToolRounds",
        hint: "chat.limits.maxToolRoundsHint",
        unit: "chat.limits.unit.rounds",
      },
    ],
  },
];

/** Every field, in the order they are shown. */
export const CHAT_LIMIT_FIELDS: LimitField[] = CHAT_LIMIT_GROUPS.flatMap(
  (group) => group.fields,
);

/**
 * The knob values, without the ranges that travel with them.
 *
 * Kept apart because the values are what is compared and saved: a set that still
 * carried `bounds` would compare unequal to itself, the two ranges being different
 * objects.
 */
export function limitValues(
  payload: ChatLimitsPayload | null | undefined,
): ChatLimits | null {
  if (!payload) return null;
  const { bounds: _bounds, ...values } = payload;
  return values;
}

/**
 * Whether two sets of values are the same, whatever the knobs turn out to be.
 *
 * Compared by what is in `saved`, so adding a knob does not mean remembering to
 * compare it here: the one place that lists the knobs is the one that shows them.
 */
export function sameLimits(saved: ChatLimits | null, draft: ChatLimits | null): boolean {
  if (!saved || !draft) return saved === draft;
  const keys = Object.keys(saved) as (keyof ChatLimits)[];
  return keys.every((key) => saved[key] === draft[key]);
}

/**
 * The fields whose value the core would refuse, by key.
 *
 * Without the ranges nothing is called wrong: the panel cannot know, and saying so
 * would be worse than letting the core be the one to answer.
 */
export function outOfRange(
  draft: ChatLimits,
  bounds?: ChatLimitBounds | null,
): (keyof ChatLimits)[] {
  if (!bounds) return [];
  return CHAT_LIMIT_FIELDS.filter((field) => {
    const span = bounds[field.key];
    if (!span) return false;
    const value = draft[field.key];
    return !Number.isInteger(value) || value < span.min || value > span.max;
  }).map((field) => field.key);
}

/**
 * Reads what a field now holds.
 *
 * A cleared field reads as zero rather than as nothing, which is outside every
 * range here and so is reported as something to fix rather than quietly saved.
 */
export function valueFromInput(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  const value = Number(trimmed);
  return Number.isFinite(value) ? Math.trunc(value) : 0;
}
