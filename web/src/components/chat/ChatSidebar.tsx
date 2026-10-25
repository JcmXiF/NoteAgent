import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MessageSquarePlus, Pencil, RotateCcw, Search, Trash2 } from "lucide-react";
import { ChatLimits } from "@/components/chat/ChatLimits";
import { DeleteChatDialog } from "@/components/chat/DeleteChatDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IconTip } from "@/components/ui/tooltip";
import type { ChatSummary } from "@/lib/api/types";
import { matchesConversation } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The conversations in this project.
 *
 * Ordered by when each was last spoken in, because that is the order a reader comes
 * back to them in. Creating one is a button rather than an empty-state action: a
 * project always has a conversation to write in, so this one is for starting another.
 *
 * Names are the reader's: a conversation takes its name from the first question until
 * they give it one, and a name is edited where it is shown rather than in a dialog.
 * Deleting is the one act that asks first, because it takes the turns with it.
 */
export function ChatSidebar() {
  const { t } = useTranslation();
  const status = useChatStore((state) => state.status);
  const error = useChatStore((state) => state.error);
  const conversations = useChatStore((state) => state.conversations);
  const currentId = useChatStore((state) => state.currentId);
  const openConversation = useChatStore((state) => state.openConversation);
  const create = useChatStore((state) => state.create);
  const rename = useChatStore((state) => state.rename);
  const load = useChatStore((state) => state.load);
  const project = useWorkspaceStore((state) => state.current);

  const [query, setQuery] = useState("");
  /** The conversation whose name is being typed, and what has been typed so far. */
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [asking, setAsking] = useState<ChatSummary | null>(null);
  /**
   * Which row the inline editor belongs to.
   *
   * Enter commits and then moves focus, which blurs the input; without this the blur
   * would commit the same name a second time. Kept in a ref because both handlers can
   * run in the same tick.
   */
  const editingRef = useRef<string | null>(null);

  const untitled = t("chat.untitled");
  const shown = conversations.filter((item) => matchesConversation(item.title, untitled, query));

  const startRename = (item: ChatSummary) => {
    editingRef.current = item.id;
    setName(item.title);
    setEditing(item.id);
  };

  const stopRename = () => {
    editingRef.current = null;
    setEditing(null);
  };

  const commitRename = (item: ChatSummary) => {
    if (editingRef.current !== item.id) return;
    stopRename();
    const next = name.trim();
    // The list already shows the automatic name for an unnamed conversation, so
    // typing that exact name is not a change.
    if (next === item.title.trim()) return;
    void rename(item.id, next);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-2 pt-1.5 pb-1">
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          disabled={status !== "ready"}
          onClick={() => void create()}
        >
          <MessageSquarePlus />
          {t("chat.new")}
        </Button>
      </div>

      {conversations.length > 0 ? (
        <div className="shrink-0 px-2 pb-1">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-1.5 size-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("chat.search")}
              aria-label={t("chat.search")}
              className="h-6 pl-6 text-[11px]"
            />
          </div>
        </div>
      ) : null}

      {error ? (
        // The list can fail on its own while the rest of the workspace is fine, so
        // the failure is reported here with the one thing that fixes it.
        <div className="flex items-start gap-1.5 px-2 pb-1">
          <p className="min-w-0 flex-1 text-[11px] text-destructive">{error}</p>
          {project ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-5 shrink-0 px-1.5"
              disabled={status === "loading"}
              onClick={() => void load(project)}
            >
              <RotateCcw />
              {t("common.retry")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {status === "loading" && conversations.length === 0 ? (
        <p className="p-3 text-[11px] text-muted-foreground">{t("chat.loading")}</p>
      ) : conversations.length === 0 ? (
        <p className="p-3 text-[11px] text-muted-foreground">{t("chat.empty")}</p>
      ) : shown.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-muted-foreground">{t("chat.searchEmpty")}</p>
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto scroll-thin">
          {shown.map((item) => {
            const active = item.id === currentId;
            const title = item.title || untitled;
            return (
              <li key={item.id}>
                <div
                  className={cn(
                    "group flex items-start gap-1 px-2 py-1.5",
                    active ? "bg-accent" : "hover:bg-accent/40",
                  )}
                >
                  {editing === item.id ? (
                    <Input
                      autoFocus
                      value={name}
                      aria-label={t("chat.rename")}
                      className="h-6 flex-1 text-[11px]"
                      onChange={(event) => setName(event.target.value)}
                      onBlur={() => commitRename(item)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitRename(item);
                        } else if (event.key === "Escape") {
                          event.preventDefault();
                          stopRename();
                        }
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        title={t("chat.renameHint")}
                        onClick={() => void openConversation(item.id)}
                        onDoubleClick={() => startRename(item)}
                      >
                        <span className="block truncate text-[11px] font-medium">{title}</span>
                        <span className="block truncate text-[10px] text-muted-foreground">
                          {t("chat.turns", { count: item.messageCount })}
                        </span>
                      </button>
                      <div className="flex shrink-0 items-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                        <IconTip label={t("chat.rename")}>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={t("chat.rename")}
                            onClick={() => startRename(item)}
                          >
                            <Pencil />
                          </Button>
                        </IconTip>
                        <IconTip label={t("chat.remove")}>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={t("chat.remove")}
                            onClick={() => setAsking(item)}
                          >
                            <Trash2 />
                          </Button>
                        </IconTip>
                      </div>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <ChatLimits />

      <DeleteChatDialog conversation={asking} onClose={() => setAsking(null)} />
    </div>
  );
}
