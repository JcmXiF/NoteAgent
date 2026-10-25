import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MessageSquare, RotateCcw, SendHorizontal, Square } from "lucide-react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { LookedCards } from "@/components/chat/LookedCards";
import { SaveRemark } from "@/components/chat/SaveRemark";
import { WriteGate } from "@/components/chat/WriteGate";
import { Button } from "@/components/ui/button";
import type { ChatTurn } from "@/lib/api/types";
import { canSend, activityKey, isAtEnd, questionBefore, spokenStep } from "@/lib/chat";
import type { LookedCard } from "@/lib/chat-cards";
import { cn } from "@/lib/utils";
import { useAcquireStore } from "@/stores/acquire-store";
import { openOriginal } from "@/stores/browse-store";
import { useChatStore } from "@/stores/chat-store";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsStore } from "@/stores/docs-store";
import { useLayoutStore, type PaneSlot } from "@/stores/layout-store";

/**
 * The conversation in front: what was asked, what was answered, and where to ask.
 *
 * The question appears as soon as it is asked and the answer is written as it
 * arrives; neither is part of the conversation until the core says it has them, so
 * a turn that fails leaves the transcript exactly as the project holds it.
 */
export function ChatView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const status = useChatStore((state) => state.status);
  const open = useChatStore((state) => state.open);
  const failedQuestion = useChatStore((state) => state.failedQuestion);
  const retry = useChatStore((state) => state.retry);
  const looked = useChatStore((state) => state.looked);
  // Which page is in front, so a card that is already the one being read says so.
  const activeDocPath = useDocsStore((state) => state.paths.main);
  const pending = useChatStore((state) => state.pending);
  const turnError = useChatStore((state) => state.turnError);
  const stopped = useChatStore((state) => state.stopped);
  const draft = useChatStore((state) => state.draft);
  const limits = useChatStore((state) => state.limits);
  const setDraft = useChatStore((state) => state.setDraft);
  const ask = useChatStore((state) => state.ask);
  const stop = useChatStore((state) => state.stop);
  const followLink = useDocsStore((state) => state.followLink);
  const followDocument = useDocsStore((state) => state.followDocument);
  const addResult = useAcquireStore((state) => state.addResult);
  const topicMap = useClipsStore((state) => state.topicMap);
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);
  const setSectionOpen = useLayoutStore((state) => state.setSectionOpen);
  const openModule = useLayoutStore((state) => state.openModule);

  /** A link that named no page, said where it was clicked. */
  const [missing, setMissing] = useState<string | null>(null);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  /** Whether the reader is reading along at the end, or has scrolled up to look back. */
  const following = useRef(true);

  const messages = open?.messages ?? [];
  const writing = pending !== null;
  // No limit of our own: the core sends the one it enforces, and until it has, the
  // length is not this side's to judge.
  const maxChars = limits?.maxContentChars ?? null;
  const sendable = canSend(draft, maxChars, writing);

  const openLink = (target: string) => {
    setMissing(followLink(target, slot) ? null : target);
  };

  // A turn of its own: the conversation was replaced, so the end of it is where the
  // reader wants to be again.
  useLayoutEffect(() => {
    following.current = true;
    const node = bodyRef.current;
    if (node) node.scrollTop = node.scrollHeight;
    setMissing(null);
  }, [open?.id]);

  // Writing along the way keeps the newest line in view, unless the reader has gone
  // back to read something earlier.
  useLayoutEffect(() => {
    const node = bodyRef.current;
    if (!node || !following.current) return;
    node.scrollTop = node.scrollHeight;
  }, [messages.length, pending?.answer, pending?.step, pending?.question]);

  // Landing on the end of a conversation whose messages arrived after the first
  // draw, which is the usual case when a conversation is opened.
  useEffect(() => {
    const node = bodyRef.current;
    if (!node) return;
    if (following.current) node.scrollTop = node.scrollHeight;
  }, [open?.messages]);

  if (status === "loading" && !open) {
    return <Notice title={t("chat.loading")} />;
  }
  if (!open) {
    return (
      <Notice
        title={t("chat.view.empty")}
        body={t("chat.view.emptyHint")}
        icon={<MessageSquare className="size-8 text-muted-foreground/25" />}
      />
    );
  }

  const submit = () => {
    if (!sendable) return;
    following.current = true;
    setMissing(null);
    void ask(draft);
  };

  /** Hands a search result to the sources list, which is where ticking happens. */
  const sendToSources = (card: LookedCard) =>
    addResult({
      title: card.label,
      url: card.url ?? "",
      site: card.where ?? "",
      siteName: card.where ?? "",
      favicon: "",
      snippet: "",
      via: "",
      why: "",
    });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-9 shrink-0 items-center gap-2 border-b px-3">
        <span className="min-w-0 truncate text-xs font-semibold">
          {open.title || t("chat.untitled")}
        </span>
        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
          {t("chat.turns", { count: messages.length })}
        </span>
      </header>

      <div
        ref={bodyRef}
        className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-3"
        onScroll={(event) => {
          following.current = isAtEnd(event.currentTarget);
        }}
      >
        {messages.length === 0 && !writing ? (
          <p className="py-8 text-center text-xs text-muted-foreground">
            {t("chat.view.firstAsk")}
          </p>
        ) : null}

        {messages.map((turn, index) => (
          <Fragment key={turn.id}>
            <Turn turn={turn} onOpenLink={openLink} />
            {turn.role === "assistant" ? (
              <>
                <SaveRemark
                  answer={turn.content}
                  question={questionBefore(messages, index)}
                  slot={slot}
                />
                {/* What the answer was built from, kept with it by the core: the
                    same rows the live turn offered, still there tomorrow. */}
                <LookedCards
                  observations={turn.cards ?? []}
                  activePath={activeDocPath}
                  onOpenPage={(page) => followDocument(page, null, slot)}
                  onOpenWeb={(url) => openOriginal(url, slot)}
                  onSendToSources={sendToSources}
                />
              </>
            ) : null}
          </Fragment>
        ))}

        {pending ? (
          <>
            <Question text={pending.question} />
            <Answer
              text={pending.answer}
              onOpenLink={openLink}
              activity={t(activityKey(spokenStep(pending.answer, pending.step)))}
            />
          </>
        ) : null}

        {/* Writing only happens from the ticks the project holds, so this says
            what is waiting and where to confirm it — it cannot confirm. */}
        {!writing ? (
          <WriteGate
            observations={looked}
            topicMap={topicMap}
            onGoCompile={() => {
              // Deciding happens in the gate now, so what is left is the step after
              // it: the compile section is where the ticks are acted on.
              setRightOpen(true);
              setSectionOpen("compile", true);
            }}
            onOpenSources={() => openModule("sources")}
          />
        ) : null}

        {!pending && stopped ? (
          <p className="mt-1.5 mb-1.5 rounded-md bg-muted px-2.5 py-1.5 text-[11px] text-muted-foreground">
            {t("chat.stopped")}
          </p>
        ) : null}
      </div>

      <div className="shrink-0 border-t">
        {missing ? (
          <p className="border-b bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground">
            {t("docs.view.missingPageNamed", { page: missing })}
          </p>
        ) : null}
        {turnError ? (
          <div className="flex items-center gap-2 border-b bg-destructive/10 px-3 py-1.5">
            <p className="min-w-0 flex-1 text-[11px] text-destructive">{turnError}</p>
            {failedQuestion ? (
              // Asking again is one try: the core skips a question it already has as
              // the last turn, so this does not put the question in twice.
              <Button variant="outline" size="sm" className="shrink-0" onClick={() => void retry()}>
                <RotateCcw />
                {t("common.retry")}
              </Button>
            ) : null}
          </div>
        ) : null}

        <form
          className="p-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter asks; Shift keeps it for a line break.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={t("chat.askPlaceholder")}
            aria-label={t("chat.askPlaceholder")}
            className={cn(
              "scroll-thin block max-h-40 w-full resize-y rounded-md border bg-transparent px-2 py-1.5",
              "text-[13px] outline-none placeholder:text-muted-foreground focus-visible:border-ring",
            )}
          />
          <div className="mt-1.5 flex items-center gap-2">
            <span className="min-w-0 truncate text-[10px] text-muted-foreground">
              {maxChars !== null && draft.trim().length > maxChars
                ? t("chat.tooLong", { max: maxChars })
                : t("chat.askHint")}
            </span>
            <span className="ml-auto shrink-0">
              {writing ? (
                // One primary action at a time: whichever of asking and stopping
                // applies is the one in reach.
                <Button variant="outline" size="sm" onClick={stop}>
                  <Square />
                  {t("chat.stop")}
                </Button>
              ) : (
                <Button type="submit" size="sm" disabled={!sendable}>
                  <SendHorizontal />
                  {t("chat.send")}
                </Button>
              )}
            </span>
          </div>
        </form>
      </div>
    </div>
  );
}

function Turn({
  turn,
  onOpenLink,
}: {
  turn: ChatTurn;
  onOpenLink: (target: string) => void;
}) {
  if (turn.role === "user") return <Question text={turn.content} />;
  return <Answer text={turn.content} onOpenLink={onOpenLink} />;
}

/** What was asked, set apart from the answer so the thread is easy to follow. */
function Question({ text }: { text: string }) {
  return (
    <p className="mt-3 mb-1.5 rounded-md bg-muted px-2.5 py-1.5 text-[13px] first:mt-0 whitespace-pre-wrap">
      {text}
    </p>
  );
}

/**
 * What was answered.
 *
 * While the turn is being written the answer carries a caret, and says what it is
 * doing until there is text to read: a turn that is still looking things up has
 * nothing else to show.
 */
function Answer({
  text,
  onOpenLink,
  activity,
}: {
  text: string;
  onOpenLink: (target: string) => void;
  activity?: string;
}) {
  if (!text) {
    if (!activity) return null;
    return <p className="py-1 text-xs text-muted-foreground">{activity}</p>;
  }
  return (
    <div className="mb-1.5">
      <ChatMarkdown body={text} onOpenLink={onOpenLink} />
      {activity ? <span className="na-caret" aria-label={activity} /> : null}
    </div>
  );
}

function Notice({
  title,
  body,
  icon,
}: {
  title: string;
  body?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon}
      <p className="text-sm font-medium">{title}</p>
      {body ? <p className="max-w-sm text-xs text-muted-foreground">{body}</p> : null}
    </div>
  );
}
