import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { SessionView } from "@/components/chat/SessionView";
import type { WaitPayload } from "@/lib/api/client";
import { useDocsStore } from "@/stores/docs-store";
import { useLayoutStore, type PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useSessionsStore } from "@/stores/sessions-store";
import { useWaitContextStore } from "@/stores/wait-context-store";

/**
 * The session in front, and the two links that leave it.
 *
 * A wiki link opens the page in this pane. A wait's deep link arms the wait for
 * the plan or compile button and brings that section into view; the button
 * itself then carries the wait into the job.
 */
export function SessionViewHost({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const currentId = useSessionsStore((state) => state.currentId);
  const status = useSessionsStore((state) => state.status);
  const followLink = useDocsStore((state) => state.followLink);
  const seq = useNavStore((state) => state.seq);
  const intent = useNavStore((state) => state.intent);

  useEffect(() => {
    if (intent?.kind === "chat" && intent.conversationId) {
      useSessionsStore.getState().open(intent.conversationId);
    }
  }, [seq, intent]);

  const openInterface = (wait: WaitPayload, sessionId: string) => {
    useWaitContextStore.getState().arm({
      sessionId,
      waitId: wait.waitId,
      job: wait.job,
      interface: wait.interface,
    });
    const layout = useLayoutStore.getState();
    layout.setRightOpen(true);
    layout.setSectionOpen(
      wait.interface === "compile_board" ? "compile" : "plan",
      true,
    );
  };

  if (!currentId) {
    return (
      <p className="p-3 text-xs text-muted-foreground">
        {status === "loading" || status === "idle"
          ? t("chat.loading")
          : t("chat.view.emptyHint")}
      </p>
    );
  }

  return (
    <SessionView
      sessionId={currentId}
      onOpenLink={(target) => {
        followLink(target, slot);
      }}
      onOpenInterface={openInterface}
      onSessionChanged={(session) =>
        useSessionsStore.getState().touch(session)
      }
    />
  );
}
