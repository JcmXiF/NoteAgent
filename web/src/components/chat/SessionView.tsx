import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { SendHorizontal, Square } from "lucide-react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { SessionCards } from "@/components/chat/SessionCards";
import { WaitCard } from "@/components/chat/WaitCard";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  errorMessage,
  type ObservationPayload,
  type WaitPayload,
} from "@/lib/api/client";
import {
  fetchSession,
  postSessionCancel,
  postSessionQuery,
  postSessionWaitCancel,
} from "@/lib/api/sessions";
import type { SessionPayload } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Activity = "think" | "tool" | "answer";

/**
 * A session as the JSONL log reads: what was asked, what was answered.
 *
 * Tool calls are steps rather than answers, so their preamble is marked and the
 * tool results stay out of the transcript. The view is told which session to
 * read; choosing that id belongs to whoever mounts it.
 */
export function SessionView({
  sessionId,
  onOpenLink = () => {},
  onOpenInterface = () => {},
}: {
  sessionId: string;
  /** A wiki link in an answer; absent leaves the link inert. */
  onOpenLink?: (target: string) => void;
  /** Opening the interface a wait points at, carrying the wait it needs. */
  onOpenInterface?: (wait: WaitPayload, sessionId: string) => void;
}) {
  const { t } = useTranslation();
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [error, setError] = useState("");
  const [queryError, setQueryError] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [activity, setActivity] = useState<Activity>("think");
  /** The cards the run reported while it was running; the committed answer
   * carries the same list after the stream ends. */
  const [observations, setObservations] = useState<ObservationPayload[]>([]);
  /** The wait the session already had when it was read, or the last one. */
  const [wait, setWait] = useState<WaitPayload | null>(null);
  /** A wait proposed while this run is still streaming. */
  const [liveWait, setLiveWait] = useState<WaitPayload | null>(null);
  const [stoppingWait, setStoppingWait] = useState(false);
  const [waitError, setWaitError] = useState("");
  /**
   * A run this tab can see but does not own: another tab asked, and this one
   * may only wait. The core decides, so this is only what the last read said.
   */
  const [runningElsewhere, setRunningElsewhere] = useState(false);

  /**
   * Whether a send is in flight, and which session it belongs to.
   *
   * The refs are what a second submit and a response that arrives after the
   * view moved on are checked against; the state is only what is drawn.
   */
  const sendingRef = useRef(false);
  const mounted = useRef(true);
  const currentSession = useRef(sessionId);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    currentSession.current = sessionId;
  }, [sessionId]);

  /** The session the view now shows, and the wait that came with it. */
  const adoptSession = (next: SessionPayload) => {
    setSession(next);
    setWait(next.wait ?? null);
  };

  useEffect(() => {
    let alive = true;
    setSession(null);
    setError("");
    setRunningElsewhere(false);
    setWait(null);
    setLiveWait(null);
    setWaitError("");
    // A stop belongs to the session it was asked in; switching sessions must
    // not leave the next session's wait looking permanently stopped.
    setStoppingWait(false);
    fetchSession(sessionId).then(
      (next) => {
        if (!alive) return;
        adoptSession(next);
        setRunningElsewhere(next.state.busy);
      },
      (reason) => {
        if (alive) setError(errorMessage(reason, t("chat.openFailed")));
      },
    );
    return () => {
      alive = false;
    };
  }, [sessionId, t]);

  const send = async () => {
    const question = draft.trim();
    if (!question || sendingRef.current || runningElsewhere) return;
    const mine = sessionId;
    const live = () => mounted.current && currentSession.current === mine;
    sendingRef.current = true;
    setDraft("");
    setQueryError("");
    setPendingQuestion(question);
    setStreaming("");
    setActivity("think");
    setObservations([]);
    setLiveWait(null);
    setWaitError("");
    setRunningElsewhere(false);
    setSending(true);
    try {
      const next = await postSessionQuery(sessionId, question, {
        onStatus: (_message, stepId) => {
          if (!live()) return;
          setActivity(stepId === "answer" || stepId === "tool" ? stepId : "think");
        },
        onToken: (text) => {
          if (live()) setStreaming((current) => current + text);
        },
        onTokenReset: () => {
          if (live()) setStreaming("");
        },
        onObservation: (observation) => {
          if (!live()) return;
          setActivity("tool");
          setObservations((current) => [...current, observation]);
        },
        onWait: (proposed) => {
          if (!live()) return;
          setLiveWait(proposed);
        },
      });
      if (live()) adoptSession(next);
    } catch (reason) {
      if (live()) {
        // Stopping was asked for: the transcript now shows where the answer
        // ended, so there is no failure to report.
        const stopped =
          reason instanceof ApiError && reason.code === "cancelled";
        if (!stopped) {
          setQueryError(errorMessage(reason, t("chat.askFailed")));
        }
        try {
          const next = await fetchSession(sessionId);
          if (live()) {
            adoptSession(next);
            // A broken stream can leave the run still going; the read says so,
            // and the composer must not offer a second one.
            if (!stopped) setRunningElsewhere(next.state.busy);
          }
        } catch {
          // The session on screen is still the last one the core committed.
        }
      }
    } finally {
      sendingRef.current = false;
      if (live()) {
        setSending(false);
        setPendingQuestion(null);
        setStreaming("");
        setLiveWait(null);
      }
    }
  };

  const stopWait = async (target: WaitPayload) => {
    if (stoppingWait) return;
    setStoppingWait(true);
    setWaitError("");
    try {
      await postSessionWaitCancel(sessionId, target.waitId);
      const next = await fetchSession(sessionId);
      if (mounted.current && currentSession.current === sessionId) {
        adoptSession(next);
      }
    } catch (reason) {
      if (mounted.current && currentSession.current === sessionId) {
        setWaitError(errorMessage(reason, t("chat.wait.stopFailed")));
      }
    } finally {
      if (mounted.current && currentSession.current === sessionId) {
        setStoppingWait(false);
      }
    }
  };

  const stop = async () => {
    try {
      await postSessionCancel(sessionId);
    } catch {
      // The run may have ended on its own; how it ended arrives on the stream.
    }
  };

  if (error) {
    return <p className="p-3 text-xs text-destructive">{error}</p>;
  }
  if (!session) {
    return <p className="p-3 text-xs text-muted-foreground">{t("chat.loading")}</p>;
  }

  // The streamed wait arrives before the session is re-read; once it is, the
  // committed wait is the same one and the live copy can go away.
  const shownWait = liveWait ?? wait;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-9 shrink-0 items-center gap-2 border-b px-3">
        <span className="min-w-0 truncate text-xs font-semibold">
          {session.title || t("chat.untitled")}
        </span>
        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
          {t("chat.turns", { count: session.messages.length })}
        </span>
      </header>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {session.messages.length === 0 && !pendingQuestion ? (
          <p className="py-8 text-center text-xs text-muted-foreground">
            {t("chat.view.firstAsk")}
          </p>
        ) : null}

        {session.messages.map((turn) => (
          <article
            key={turn.id}
            className={cn("mb-3", turn.isStep ? "text-muted-foreground" : undefined)}
          >
            {turn.isStep ? (
              <p className="mb-1 text-[11px]">{t("chat.step.tool")}</p>
            ) : null}
            {turn.role === "user" ? (
              <p className="text-xs whitespace-pre-wrap">{turn.content}</p>
            ) : (
              <>
                <ChatMarkdown body={turn.content} onOpenLink={onOpenLink} />
                {turn.cards?.length ? (
                  <SessionCards observations={turn.cards} onOpen={onOpenLink} />
                ) : null}
              </>
            )}
            {turn.stopReason === "aborted" ? (
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t("chat.aborted")}
              </p>
            ) : null}
          </article>
        ))}

        {shownWait ? (
          <WaitCard
            wait={shownWait}
            onOpen={(target) => onOpenInterface(target, sessionId)}
            onStop={
              sending
                ? undefined
                : () => {
                    void stopWait(shownWait);
                  }
            }
            stopping={stoppingWait}
          />
        ) : null}
        {waitError ? (
          <p className="mb-2 text-[11px] text-destructive">{waitError}</p>
        ) : null}

        {pendingQuestion ? (
          <p className="mt-3 mb-1.5 rounded-md bg-muted px-2.5 py-1.5 text-[13px] whitespace-pre-wrap">
            {pendingQuestion}
          </p>
        ) : null}

        {sending ? (
          <article className="mb-3">
            <p className="mb-1 text-[11px] text-muted-foreground">
              {t(`chat.activity.${activity}`)}
            </p>
            <SessionCards observations={observations} onOpen={onOpenLink} />
            {streaming ? (
              <ChatMarkdown body={streaming} onOpenLink={onOpenLink} />
            ) : null}
          </article>
        ) : null}
      </div>

      <div className="shrink-0 border-t">
        {runningElsewhere ? (
          <p className="border-b bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground">
            {t("chat.busy")}
          </p>
        ) : null}
        {queryError ? (
          <p className="border-b bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
            {queryError}
          </p>
        ) : null}
        <form
          className="p-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            rows={2}
            disabled={runningElsewhere}
            placeholder={t("chat.askPlaceholder")}
            aria-label={t("chat.askPlaceholder")}
            className={cn(
              "scroll-thin block max-h-40 w-full resize-y rounded-md border bg-transparent px-2 py-1.5",
              "text-[13px] outline-none placeholder:text-muted-foreground focus-visible:border-ring",
            )}
          />
          <div className="mt-1.5 flex items-center gap-2">
            <span className="min-w-0 truncate text-[10px] text-muted-foreground">
              {t("chat.askHint")}
            </span>
            <span className="ml-auto shrink-0">
              {sending ? (
                // One primary action at a time: asking, or stopping what was
                // asked, whichever this moment allows.
                <Button variant="outline" size="sm" onClick={() => void stop()}>
                  <Square />
                  {t("chat.stop")}
                </Button>
              ) : (
                <Button
                  type="submit"
                  size="sm"
                  disabled={runningElsewhere || !draft.trim()}
                >
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
