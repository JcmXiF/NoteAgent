import { Check, Compass, FilePlus2, FolderOpen, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useLayoutStore } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { cn } from "@/lib/utils";

/**
 * What the workspace says when there is nothing in it yet.
 *
 * Two moments, one component file. With no project at all, the page is the whole
 * product pitch and the two ways to begin — a reader cannot be asked to pick from
 * a list that does not exist. With a project but no pages, it is the three steps
 * from "a folder of sources" to "the first page", one button at a time: the pour
 * of options is what makes a first run stall, so only the step being worked on
 * carries a button.
 */
export function FirstRun() {
  const { t } = useTranslation();
  const setDialog = useWorkspaceStore((state) => state.setDialog);
  const loading = useWorkspaceStore((state) => state.loading);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 p-8 text-center">
      <div className="flex size-11 items-center justify-center rounded-xl bg-primary/10">
        <Sparkles className="size-5 text-primary" />
      </div>
      <div className="flex max-w-md flex-col gap-2">
        <h1 className="text-base font-medium">{t("welcome.title")}</h1>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("welcome.body")}</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button disabled={loading} onClick={() => setDialog("create")}>
          <FilePlus2 />
          {t("project.new")}
        </Button>
        <Button variant="outline" disabled={loading} onClick={() => setDialog("open")}>
          <FolderOpen />
          {t("project.open.action")}
        </Button>
      </div>
      <p className="max-w-sm text-[11px] leading-relaxed text-muted-foreground">
        {t("welcome.footnote")}
      </p>
    </div>
  );
}

/** The three steps, and which of them is next. */
export function GettingStarted({
  sources,
  selections,
}: {
  sources: number;
  selections: number;
}) {
  const { t } = useTranslation();
  const openModule = useLayoutStore((state) => state.openModule);
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);
  const setSectionOpen = useLayoutStore((state) => state.setSectionOpen);

  const done = [sources > 0, selections > 0, false];
  const current = done[0] ? (done[1] ? 2 : 1) : 0;

  const start = (step: number) => {
    if (step === 2) {
      // Compiling happens in the panel beside the workbench, so the step that
      // says "compile" has to show both halves of it rather than only a module.
      setRightOpen(true);
      setSectionOpen("compile", true);
    }
    openModule("sources");
  };

  const steps = [
    {
      label: t("welcome.step.sources"),
      hint: t("welcome.step.sourcesHint"),
      action: t("welcome.step.sourcesAction"),
    },
    {
      label: t("welcome.step.selection"),
      hint: t("welcome.step.selectionHint"),
      action: t("welcome.step.selectionAction"),
    },
    {
      label: t("welcome.step.compile"),
      hint: t("welcome.step.compileHint"),
      action: t("welcome.step.compileAction"),
    },
  ];

  return (
    <div className="flex h-full flex-col items-center justify-center p-8">
      <div className="flex w-full max-w-md flex-col gap-4 text-left">
        <div className="flex items-center gap-2">
          <Compass className="size-4 text-muted-foreground" />
          <h1 className="text-sm font-medium">{t("welcome.startTitle")}</h1>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("welcome.startBody")}
        </p>
        <ol className="flex flex-col gap-1">
          {steps.map((step, index) => (
            <li
              key={step.label}
              className={cn(
                "flex items-start gap-2.5 rounded-lg border px-3 py-2",
                index === current ? "border-primary/40 bg-primary/5" : "border-transparent",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-medium",
                  done[index]
                    ? "bg-primary text-primary-foreground"
                    : index === current
                      ? "border border-primary/50 text-primary"
                      : "border text-muted-foreground",
                )}
              >
                {done[index] ? <Check className="size-2.5" /> : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p
                  className={cn(
                    "text-xs font-medium",
                    index > current ? "text-muted-foreground" : undefined,
                  )}
                >
                  {step.label}
                </p>
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  {step.hint}
                </p>
                {index === current ? (
                  <Button size="sm" className="mt-1.5" onClick={() => start(index)}>
                    {step.action}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
