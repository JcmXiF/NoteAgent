import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AppearanceSection } from "@/components/settings/AppearanceSection";
import { DiagnosticsSection } from "@/components/settings/DiagnosticsSection";
import { EffectiveConfigSection } from "@/components/settings/EffectiveConfigSection";
import { GenerationSection } from "@/components/settings/GenerationSection";
import { ProfilesSection } from "@/components/settings/ProfilesSection";
import { ProjectSection } from "@/components/settings/ProjectSection";
import { SearchSection } from "@/components/settings/SearchSection";
import { fetchSettings } from "@/lib/api/settings";
import { errorMessage } from "@/lib/api/client";
import i18n from "@/i18n";
import type { InstallSettings } from "@/lib/install-settings";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layout-store";

const GROUPS = [
  {
    scope: "settings.scope.install",
    ids: ["model", "generation", "search", "effective", "diagnostics"],
  },
  { scope: "settings.scope.project", ids: ["project"] },
  { scope: "settings.scope.window", ids: ["appearance"] },
] as const;

type SectionId = (typeof GROUPS)[number]["ids"][number];

/** Every section stays mounted; leaving one must not lose a draft in it. */
function Pane({ visible, children }: { visible: boolean; children: ReactNode }) {
  return <div className={cn(visible ? undefined : "hidden")}>{children}</div>;
}

/**
 * Settings replace the workspace rather than sitting beside a page.
 *
 * A model connection is not something to read next to a note, and a second
 * pane of it would be the same form twice. The rail stays, so leaving is a
 * matter of choosing a page again.
 */
export function SettingsView() {
  const { t } = useTranslation();
  const setSettingsOpen = useLayoutStore((state) => state.setSettingsOpen);
  const setSettingsDirty = useLayoutStore((state) => state.setSettingsDirty);
  const confirm = useLayoutStore((state) => state.settingsConfirm);
  const confirmLeave = useLayoutStore((state) => state.confirmSettingsLeave);
  const cancelLeave = useLayoutStore((state) => state.cancelSettingsLeave);

  const [section, setSection] = useState<SectionId>("model");
  const [install, setInstall] = useState<InstallSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dirty, setDirty] = useState({
    model: false,
    generation: false,
    search: false,
    project: false,
  });

  const onModelDirty = useCallback((value: boolean) => {
    setDirty((current) => (current.model === value ? current : { ...current, model: value }));
  }, []);
  const onGenerationDirty = useCallback((value: boolean) => {
    setDirty((current) =>
      current.generation === value ? current : { ...current, generation: value },
    );
  }, []);
  const onSearchDirty = useCallback((value: boolean) => {
    setDirty((current) => (current.search === value ? current : { ...current, search: value }));
  }, []);
  const onProjectDirty = useCallback((value: boolean) => {
    setDirty((current) => (current.project === value ? current : { ...current, project: value }));
  }, []);

  const unsaved = Object.values(dirty).some(Boolean);
  const changedBySection: Partial<Record<SectionId, boolean>> = {
    model: dirty.model,
    generation: dirty.generation,
    search: dirty.search,
    project: dirty.project,
  };
  useEffect(() => {
    setSettingsDirty(unsaved);
  }, [unsaved, setSettingsDirty]);
  useEffect(() => () => setSettingsDirty(false), [setSettingsDirty]);

  const load = useCallback(async () => {
    try {
      const next = await fetchSettings();
      setInstall(next);
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err, i18n.t("settings.loadFailed")));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const waiting = loadError ? (
    <div className="flex flex-col items-start gap-2">
      <p className="text-sm text-destructive">{loadError}</p>
      <Button type="button" variant="outline" onClick={() => void load()}>
        {t("common.retry")}
      </Button>
    </div>
  ) : (
    <p className="text-sm text-muted-foreground">{t("settings.loading")}</p>
  );

  return (
    <div className="flex h-full min-h-0">
      <nav className="flex w-44 shrink-0 flex-col gap-0.5 border-r p-3">
        <div className="px-2 pb-2">
          <div className="text-sm font-semibold">{t("settings.title")}</div>
          {install?.version ? (
            <div className="text-[10px] text-muted-foreground">
              {t("settings.version", { version: install.version })}
            </div>
          ) : null}
        </div>
        {GROUPS.map((group) => (
          <div key={group.scope} className="flex flex-col gap-0.5">
            <div className="px-2 pt-2 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              {t(group.scope)}
            </div>
            {group.ids.map((id) => {
              const changed = changedBySection[id] ?? false;
              return (
                <button
                  key={id}
                  type="button"
                  aria-current={section === id ? "page" : undefined}
                  onClick={() => setSection(id)}
                  className={cn(
                    "flex items-center rounded-md px-2 py-1.5 text-left text-sm",
                    section === id
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                  )}
                >
                  {t(`settings.nav.${id}`)}
                  {changed ? (
                    <span className="ml-auto size-1.5 rounded-full bg-primary" />
                  ) : null}
                </button>
              );
            })}
          </div>
        ))}
        <Button
          type="button"
          variant="ghost"
          className="mt-auto justify-start"
          onClick={() => setSettingsOpen(false)}
        >
          {t("settings.close")}
        </Button>
      </nav>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div
          className={cn(
            "mx-auto flex w-full flex-col px-6 py-6",
            section === "model" ? "max-w-4xl" : "max-w-xl",
          )}
        >
          {install ? (
            <Pane visible={section === "model"}>
              <ProfilesSection
                install={install}
                onSaved={setInstall}
                onDirty={onModelDirty}
                onReload={load}
              />
            </Pane>
          ) : section === "model" ? (
            waiting
          ) : null}
          {install ? (
            <Pane visible={section === "generation"}>
              <GenerationSection
                install={install}
                onSaved={setInstall}
                onDirty={onGenerationDirty}
                onReload={load}
              />
            </Pane>
          ) : section === "generation" ? (
            waiting
          ) : null}
          {install ? (
            <Pane visible={section === "search"}>
              <SearchSection install={install} onSaved={setInstall} onDirty={onSearchDirty} />
            </Pane>
          ) : section === "search" ? (
            waiting
          ) : null}
          {install ? (
            <Pane visible={section === "effective"}>
              <EffectiveConfigSection install={install} />
            </Pane>
          ) : section === "effective" ? (
            waiting
          ) : null}
          <Pane visible={section === "project"}>
            <ProjectSection onDirty={onProjectDirty} />
          </Pane>
          <Pane visible={section === "diagnostics"}>
            <DiagnosticsSection onOpenSettings={setSection} />
          </Pane>
          <Pane visible={section === "appearance"}>
            <AppearanceSection />
          </Pane>
        </div>
      </div>

      <Dialog
        open={confirm}
        onOpenChange={(open) => {
          if (!open) cancelLeave();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("settings.leave.title")}</DialogTitle>
            <DialogDescription>{t("settings.leave.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={cancelLeave}>
              {t("settings.leave.stay")}
            </Button>
            <Button type="button" onClick={confirmLeave}>
              {t("settings.leave.discard")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
