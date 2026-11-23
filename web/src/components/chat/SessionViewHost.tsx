import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { SessionView } from "@/components/chat/SessionView";
import { useDocsStore } from "@/stores/docs-store";
import type { PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useSessionsStore } from "@/stores/sessions-store";

/**
 * The session in front, and the link that leaves it.
 *
 * A wiki link opens the page in this pane.
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
      slot={slot}
      onOpenLink={(target) => {
        followLink(target, slot);
      }}
      onSessionChanged={(session) =>
        useSessionsStore.getState().touch(session)
      }
    />
  );
}
