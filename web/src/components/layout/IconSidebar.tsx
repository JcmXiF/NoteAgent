import { Command, ListTodo, Moon, PanelLeft, Search, Settings, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MODULE_LIST } from "@/modules";
import { IconTip } from "@/components/ui/tooltip";
import { LanguageToggle } from "@/components/layout/LanguageToggle";
import { cn } from "@/lib/utils";
import { shortcut } from "@/lib/platform";
import { leftSectionOpen, useLayoutStore } from "@/stores/layout-store";
import { useUiStore } from "@/stores/ui-store";
import { usePaletteStore } from "@/stores/palette-store";
import { useActivityStore } from "@/stores/activity-store";

/**
 * Icon rail. A plain click brings a module to the front pane, leaving whatever is
 * beside it where it is; holding the platform modifier opens it beside the current
 * one instead.
 */
export function IconSidebar() {
  const { t } = useTranslation();
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  const openModule = useLayoutStore((state) => state.openModule);
  const leftOpen = useLayoutStore((state) => state.leftOpen);
  const setLeftOpen = useLayoutStore((state) => state.setLeftOpen);

  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);

  const running = useActivityStore((state) =>
    state.items.some((item) => item.phase === "running"),
  );
  const activeJobs = useActivityStore(
    (state) => state.items.filter((item) => item.phase === "running").length,
  );
  const hasShell = useLayoutStore((state) => state.hasShell);
  const setJobsOpen = useLayoutStore((state) => state.setJobsOpen);
  const settingsOpen = useLayoutStore((state) => state.settingsOpen);
  const setSettingsOpen = useLayoutStore((state) => state.setSettingsOpen);
  const leftSections = useLayoutStore((state) => state.leftSections);
  const setLeftSectionOpen = useLayoutStore((state) => state.setLeftSectionOpen);

  // The shell draws its browser over the page, so the list has to come in front of
  // it; anywhere else the sidebar is the place for it, and this entry unfolds it.
  const jobsOpenInSidebar = leftSectionOpen(leftSections, "jobs");
  const onJobs = () => {
    if (hasShell) {
      setJobsOpen(true);
      return;
    }
    if (leftOpen && jobsOpenInSidebar) {
      setLeftSectionOpen("jobs", false);
      return;
    }
    setLeftOpen(true);
    setLeftSectionOpen("jobs", true);
  };

  const themeOrder = ["light", "dark", "system"] as const;
  const nextTheme = themeOrder[(themeOrder.indexOf(theme) + 1) % themeOrder.length];
  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : Command;

  return (
    <aside className="flex w-12 shrink-0 flex-col items-center gap-1 border-r bg-sidebar py-2">
      <div
        className="mb-1 flex size-8 items-center justify-center rounded-md bg-primary text-[11px] font-semibold text-primary-foreground"
        title={t("app.brandTitle")}
      >
        {t("app.brand")}
      </div>

      {/* One search over the whole project, reachable without knowing a shortcut
          and without first choosing a view to search in. */}
      <IconTip label={`${t("palette.open")} (${shortcut("K")})`}>
        <button
          type="button"
          aria-label={t("palette.open")}
          onClick={() => usePaletteStore.getState().setOpen(true)}
          className="mb-1 flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
        >
          <Search className="size-[18px]" />
        </button>
      </IconTip>

      {MODULE_LIST.map((meta, index) => {
        const active = view === meta.id || side === meta.id;
        const inSide = side === meta.id;
        const Icon = meta.icon;
        const key = shortcut(index + 1);
        return (
          <IconTip
            key={meta.id}
            label={`${t(`${meta.i18nKey}.label`)} — ${t(`${meta.i18nKey}.hint`)} (${key}${
              inSide ? `, ${t("nav.alreadyOpenInSplit")}` : ""
            })`}
          >
            <button
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={(event) =>
                openModule(meta.id, { side: event.metaKey || event.ctrlKey })
              }
              className={cn(
                "relative flex size-9 items-center justify-center rounded-md transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
              )}
            >
              <Icon className="size-[18px]" />
              {inSide ? (
                <span className="absolute right-1 bottom-1 size-1.5 rounded-full bg-primary" />
              ) : null}
            </button>
          </IconTip>
        );
      })}

      <div className="mt-auto flex flex-col items-center gap-1">
        <IconTip label={t("activity.title")}>
          <button
            type="button"
            aria-label={t("activity.title")}
            onClick={onJobs}
            className="relative flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
          >
            <ListTodo className="size-[18px]" />
            {/* Counted, because the point of the entry is to say how much is going
                on without the reader having to look. */}
            {activeJobs > 0 ? (
              <span className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-primary px-1 text-center text-[9px] leading-4 font-semibold text-primary-foreground tabular-nums">
                {activeJobs}
              </span>
            ) : running ? (
              <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-primary" />
            ) : null}
          </button>
        </IconTip>

        {!leftOpen ? (
          <IconTip label={`${t("nav.toggleLeft")} (${shortcut("B")})`}>
            <button
              type="button"
              onClick={() => setLeftOpen(true)}
              className="relative flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
            >
              <PanelLeft className={cn("size-[18px]", running && "animate-pulse")} />
              {running ? (
                <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-primary" />
              ) : null}
            </button>
          </IconTip>
        ) : null}

        <IconTip
          label={t("theme.switchTo", {
            current: t(`theme.${theme}`),
            next: t(`theme.${nextTheme}`),
          })}
        >
          <button
            type="button"
            onClick={() => setTheme(nextTheme)}
            className="flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
          >
            <ThemeIcon className="size-[18px]" />
          </button>
        </IconTip>

        <LanguageToggle />

        <IconTip label={`${t("settings.title")} — ${t("settings.hint")}`}>
          <button
            type="button"
            aria-label={t("settings.title")}
            aria-current={settingsOpen ? "page" : undefined}
            onClick={() => setSettingsOpen(!settingsOpen)}
            className={cn(
              "flex size-9 items-center justify-center rounded-md",
              settingsOpen
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
            )}
          >
            <Settings className="size-[18px]" />
          </button>
        </IconTip>
      </div>
    </aside>
  );
}
