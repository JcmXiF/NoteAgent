import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Download,
  FileText,
  FileUp,
  FolderInput,
  FolderOpen,
  Loader2,
  Search,
  Settings,
  StickyNote,
  type LucideIcon,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api/client";
import { searchProject, type ProjectSearch, type ProjectSearchHit } from "@/lib/api/pages";
import { matchesWords } from "@/lib/chat";
import { downloadProjectArchive } from "@/lib/project-export";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { usePaletteStore } from "@/stores/palette-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/** Which list a result came from, which is also how it is grouped on screen. */
type Group = "pages" | "notes" | "sources";

type HitRow = { group: Group; hit: ProjectSearchHit };

/** What the palette can do besides find things. */
type ActionId = "source" | "import" | "export" | "settings";

type Action = {
  id: ActionId;
  label: string;
  hint: string;
  icon: LucideIcon;
  /** False for an action a project is needed for, with the hint saying so. */
  disabled: boolean;
  run: () => void;
};

/** A row is either something to do or something that was found. */
type Row = { kind: "action"; action: Action } | ({ kind: "hit" } & HitRow);

const GROUP_ICON = {
  pages: FileText,
  notes: StickyNote,
  sources: FolderOpen,
} as const;

/**
 * One search over the whole project, and the handful of things worth doing from
 * anywhere.
 *
 * Results come first when something has been typed, and the actions come first when
 * nothing has: an empty box should not be an empty box. Pages come before the remarks
 * written about them and the sources they were written from — the order a reader is
 * likely to want, and the order the core ranks them in.
 *
 * Choosing a result opens it where it lives. Choosing an action closes the palette
 * first, because an action changes the window the palette is drawn on.
 */
export function CommandPalette() {
  const { t } = useTranslation();
  const open = usePaletteStore((state) => state.open);
  const setOpen = usePaletteStore((state) => state.setOpen);
  const project = useWorkspaceStore((state) => state.current);
  const go = useNavStore((state) => state.go);

  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ProjectSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  // A search is about the palette being open; closing it forgets the last one.
  useEffect(() => {
    if (open) return;
    setQuery("");
    setFound(null);
    setError(null);
    setLoading(false);
    setActive(0);
  }, [open]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      usePaletteStore.getState().toggle();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open || !project) return undefined;
    const text = query.trim();
    if (!text) {
      setFound(null);
      setLoading(false);
      return undefined;
    }
    let live = true;
    setLoading(true);
    // Typing a name should not send a request per keystroke; the pause is what
    // the reader means by having finished the word.
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const payload = await searchProject(project, text, 6);
          if (!live) return;
          setFound(payload);
          setError(null);
          setActive(0);
        } catch (err) {
          if (!live) return;
          setError(errorMessage(err, t("palette.failed")));
        } finally {
          if (live) setLoading(false);
        }
      })();
    }, 160);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [open, project, query, t]);

  const needsProject = project === null;
  const actions = useMemo<Action[]>(
    () => [
      {
        id: "source",
        label: t("palette.action.source"),
        hint: needsProject
          ? t("palette.action.needsProject")
          : t("palette.action.sourceHint"),
        icon: FileUp,
        disabled: needsProject,
        run: () => go({ kind: "sources" }, { placement: "replace" }),
      },
      {
        id: "import",
        label: t("palette.action.import"),
        hint: t("palette.action.importHint"),
        icon: FolderInput,
        // Importing a folder is also how a reader gets their first project, so it
        // is the one action that works with none open.
        disabled: false,
        run: () => useWorkspaceStore.getState().setDialog("open"),
      },
      {
        id: "export",
        label: t("palette.action.export"),
        hint: needsProject
          ? t("palette.action.needsProject")
          : t("palette.action.exportHint"),
        icon: Download,
        disabled: needsProject,
        run: () => {
          const current = useWorkspaceStore.getState().current;
          if (current) downloadProjectArchive(current);
        },
      },
      {
        id: "settings",
        label: t("palette.action.settings"),
        hint: t("palette.action.settingsHint"),
        icon: Settings,
        disabled: false,
        run: () => useLayoutStore.getState().setSettingsOpen(true),
      },
    ],
    [go, needsProject, t],
  );

  const hits = useMemo<HitRow[]>(() => {
    if (!found) return [];
    return [
      ...found.pages.map((hit) => ({ group: "pages" as const, hit })),
      ...found.notes.map((hit) => ({ group: "notes" as const, hit })),
      ...found.sources.map((hit) => ({ group: "sources" as const, hit })),
    ];
  }, [found]);

  const matches = useMemo(
    () => actions.filter((action) => matchesWords(`${action.label} ${action.hint}`, query)),
    [actions, query],
  );

  const rows = useMemo<Row[]>(
    () => [
      ...matches.map((action) => ({ kind: "action" as const, action })),
      ...hits.map((row) => ({ kind: "hit" as const, ...row })),
    ],
    [hits, matches],
  );

  // The highlighted row has to be visible even when the keyboard moved it past
  // the bottom of the list.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const row = list.querySelector<HTMLElement>(`[data-row="${active}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [active, rows.length]);

  const choose = (row: Row) => {
    if (row.kind === "action") {
      if (row.action.disabled) return;
      setOpen(false);
      row.action.run();
      return;
    }
    setOpen(false);
    if (row.group === "notes") {
      go(
        { kind: "notes", path: row.hit.page || row.hit.path, noteId: row.hit.id },
        { placement: "replace" },
      );
      return;
    }
    go({ kind: "docs", path: row.hit.path }, { placement: "replace" });
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (rows.length === 0 ? 0 : (index + 1) % rows.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) =>
        rows.length === 0 ? 0 : (index - 1 + rows.length) % rows.length,
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const row = rows[active];
      if (row) choose(row);
    }
  };

  const groups: { id: Group; rows: HitRow[] }[] = [
    { id: "pages", rows: hits.filter((row) => row.group === "pages") },
    { id: "notes", rows: hits.filter((row) => row.group === "notes") },
    { id: "sources", rows: hits.filter((row) => row.group === "sources") },
  ];
  const searching = query.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="top-[15%] translate-y-0 gap-0 p-0 sm:max-w-xl"
      >
        <DialogTitle className="sr-only">{t("palette.title")}</DialogTitle>
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={t("palette.placeholder")}
            aria-label={t("palette.title")}
            autoFocus
            className="h-11 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {loading ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
          ) : null}
        </div>

        <div ref={listRef} className="max-h-[55vh] overflow-y-auto scroll-thin p-1">
          {error ? (
            <p className="px-3 py-2 text-center text-[11px] text-destructive">{error}</p>
          ) : null}
          {rows.length === 0 ? (
            <p className="px-3 py-6 text-center text-[11px] text-muted-foreground">
              {searching ? t("palette.empty") : t("palette.hint")}
            </p>
          ) : null}

          {matches.length > 0 ? (
            <div className="mb-1 last:mb-0">
              <p className="px-2 py-1 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                {t("palette.group.actions")}
              </p>
              {matches.map((action) => {
                const index = matches.indexOf(action);
                const Icon = action.icon;
                return (
                  <button
                    key={action.id}
                    type="button"
                    data-row={index}
                    disabled={action.disabled}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => {
                      const row = rows[index];
                      if (row) choose(row);
                    }}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                      index === active ? "bg-accent" : "hover:bg-accent/50",
                      action.disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
                    )}
                  >
                    <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">{action.label}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {action.hint}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : null}

          {!project && !searching ? (
            <p className="px-2 py-1 text-[10px] text-muted-foreground">{t("palette.noProject")}</p>
          ) : null}

          {groups.map((group) => {
            if (group.rows.length === 0) return null;
            const Icon = GROUP_ICON[group.id];
            const total = found?.counts[group.id] ?? group.rows.length;
            const rest = total - group.rows.length;
            return (
              <div key={group.id} className="mb-1 last:mb-0">
                <p className="px-2 py-1 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                  {t(`palette.group.${group.id}`)}
                </p>
                {group.rows.map((row) => {
                  // Rows are the actions followed by the hits, so a hit's place in
                  // the keyboard's order is its place in this list plus the actions.
                  const index = matches.length + hits.indexOf(row);
                  return (
                    <button
                      key={`${group.id}:${row.hit.path}:${row.hit.id ?? ""}`}
                      type="button"
                      data-row={index}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => choose({ kind: "hit", ...row })}
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                        index === active ? "bg-accent" : "hover:bg-accent/50",
                      )}
                    >
                      <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">
                          {row.hit.title}
                        </span>
                        <span className="block truncate text-[10px] text-muted-foreground">
                          {row.hit.snippet || row.hit.slug || row.hit.path}
                        </span>
                      </span>
                    </button>
                  );
                })}
                {rest > 0 ? (
                  <p className="px-2 py-0.5 text-[10px] text-muted-foreground">
                    {t("palette.more", { count: rest })}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>

        <div className="flex items-center justify-between border-t px-3 py-1.5 text-[10px] text-muted-foreground">
          <span>{t("palette.keys")}</span>
          <span>{t("palette.esc")}</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
