import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  MessageSquarePlus,
  Pencil,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import { DeleteChatDialog } from "@/components/chat/DeleteChatDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IconTip } from "@/components/ui/tooltip";
import type { SessionSummary } from "@/lib/api/types";
import { matchesConversation } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { useSessionsStore } from "@/stores/sessions-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The sessions in this project, newest first.
 *
 * The versioned core stores them as JSONL; this list is the reader's way in.
 * A session takes its name from the first question until it is named here, and
 * deleting asks first because it takes the transcript with it.
 */
export function SessionSidebar() {
  const { t } = useTranslation();
  const status = useSessionsStore((state) => state.status);
  const error = useSessionsStore((state) => state.error);
  const sessions = useSessionsStore((state) => state.sessions);
  const currentId = useSessionsStore((state) => state.currentId);
  const migrationFailed = useSessionsStore((state) => state.migrationFailed);
  const load = useSessionsStore((state) => state.load);
  const reset = useSessionsStore((state) => state.reset);
  const open = useSessionsStore((state) => state.open);
  const create = useSessionsStore((state) => state.create);
  const rename = useSessionsStore((state) => state.rename);
  const remove = useSessionsStore((state) => state.remove);
  const project = useWorkspaceStore((state) => state.current);

  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [asking, setAsking] = useState<SessionSummary | null>(null);
  const editingRef = useRef<string | null>(null);

  useEffect(() => {
    if (project) void load(project);
    else reset();
  }, [project, load, reset]);

  const untitled = t("chat.untitled");
  const shown = sessions.filter((item) =>
    matchesConversation(item.title, untitled, query),
  );

  const startRename = (item: SessionSummary) => {
    editingRef.current = item.id;
    setName(item.title);
    setEditing(item.id);
  };

  const stopRename = () => {
    editingRef.current = null;
    setEditing(null);
  };

  const commitRename = (item: SessionSummary) => {
    if (editingRef.current !== item.id) return;
    stopRename();
    const next = name.trim();
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
          disabled={!project || status === "loading"}
          onClick={() => void create()}
        >
          <MessageSquarePlus />
          {t("chat.new")}
        </Button>
      </div>

      {sessions.length > 0 ? (
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
        <div className="flex items-start gap-1.5 px-2 pb-1">
          <p className="min-w-0 flex-1 text-[11px] text-destructive">
            {error}
          </p>
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

      {migrationFailed > 0 ? (
        <p className="px-2 pb-1 text-[11px] text-warning">
          {t("chat.migrationFailed", { count: migrationFailed })}
        </p>
      ) : null}

      {status === "loading" && sessions.length === 0 ? (
        <p className="p-3 text-[11px] text-muted-foreground">
          {t("chat.loading")}
        </p>
      ) : sessions.length === 0 ? (
        <p className="p-3 text-[11px] text-muted-foreground">
          {t("chat.empty")}
        </p>
      ) : shown.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-muted-foreground">
          {t("chat.searchEmpty")}
        </p>
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
                        onClick={() => open(item.id)}
                        onDoubleClick={() => startRename(item)}
                      >
                        <span className="block truncate text-[11px] font-medium">
                          {title}
                        </span>
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

      <DeleteChatDialog
        conversation={asking}
        onClose={() => setAsking(null)}
        onDelete={(id) => remove(id)}
      />
    </div>
  );
}
