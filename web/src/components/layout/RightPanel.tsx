import { AlertCircle, ChevronRight, Hammer, ListChecks, PanelRightClose, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { IconTip } from "@/components/ui/tooltip";
import { CollectionList } from "@/components/source/CollectionList";
import { PlanBoard } from "@/components/plan/PlanBoard";
import { CompilePanel } from "@/components/plan/CompilePanel";
import { AttentionInbox } from "@/components/plan/AttentionInbox";
import { shortcut } from "@/lib/platform";
import { cn } from "@/lib/utils";
import {
  RIGHT_SECTION_LABEL,
  RIGHT_SECTIONS,
  sectionIsOpen,
  useLayoutStore,
  type RightSectionId,
} from "@/stores/layout-store";
import { useSectionCounts } from "@/stores/right-sections";

/** What draws each section: the block itself. */
const SECTION_BODY: Record<RightSectionId, () => React.ReactNode> = {
  attention: () => <AttentionInbox />,
  selection: () => <CollectionList />,
  plan: () => <PlanBoard />,
  compile: () => <CompilePanel />,
};

/**
 * How each section announces itself.
 *
 * The heading carries the name, so the body does not repeat it; what the body does
 * keep is its number, which it reports up so the heading can show it while the
 * section is folded.
 */
const SECTION_LOOK: Record<RightSectionId, { icon: typeof AlertCircle; tone?: string; hint?: string }> = {
  attention: { icon: AlertCircle, tone: "text-warning", hint: "attention.hint" },
  selection: { icon: ListChecks },
  plan: { icon: Sparkles },
  compile: { icon: Hammer },
};

/**
 * What is being read for compilation, and the workspace that plans and writes
 * pages from it.
 *
 * It stands beside the workspace rather than inside a module because what it lists
 * comes from several views; inside one, it would vanish when the reader looks at
 * another. Which parts of it matter does follow from what is being read, so each
 * section folds, and the ones belonging to the module in front are the ones open.
 */
export function RightPanel() {
  const { t } = useTranslation();
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);
  // A module's own work shows in the sections that belong to it, whichever pane it
  // is in: the panel is about the workspace, not about one pane of it.
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  const focused = useLayoutStore((state) => state.focused);
  const module = focused === "side" && side ? side : view;
  const rightFolds = useLayoutStore((state) => state.rightFolds);
  const setSectionOpen = useLayoutStore((state) => state.setSectionOpen);
  const counts = useSectionCounts((state) => state.counts);

  return (
    <aside className="flex h-full min-h-0 flex-col border-l bg-sidebar">
      <header className="flex h-9 shrink-0 items-center gap-1.5 border-b px-2">
        {/* Named for the whole column rather than for one of its parts: the parts
            each carry their own heading just below, so a name taken from any one
            of them both misnames the rest and says that part twice. */}
        <span className="text-xs font-semibold">{t("details.title")}</span>
        <div className="ml-auto">
          <IconTip label={`${t("nav.hideRight")} (${shortcut("E")})`}>
            <Button variant="ghost" size="icon-xs" onClick={() => setRightOpen(false)}>
              <PanelRightClose />
            </Button>
          </IconTip>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scroll-thin">
        {RIGHT_SECTIONS.map((section) => {
          const shown = sectionIsOpen({ rightFolds }, section, module);
          const Body = SECTION_BODY[section];
          const look = SECTION_LOOK[section];
          const Icon = look.icon;
          const count = counts[section] ?? null;
          return (
            <section key={section} className="flex min-h-0 shrink-0 flex-col border-b last:border-b-0">
              <button
                type="button"
                aria-expanded={shown}
                onClick={() => setSectionOpen(section, !shown)}
                className="flex shrink-0 items-center gap-1 px-2 py-1 text-left text-[11px] font-medium text-muted-foreground hover:bg-accent/40"
              >
                <ChevronRight
                  className={cn("size-3 shrink-0 transition-transform", shown && "rotate-90")}
                />
                <Icon className={cn("size-3.5 shrink-0", look.tone ?? "text-muted-foreground")} />
                <span className="shrink-0">{t(RIGHT_SECTION_LABEL[section])}</span>
                {/* What the section holds, readable whether it is open or not: a
                    folded section that says nothing hides what it has. */}
                {count !== null ? (
                  <Badge tone={look.tone ? "warning" : undefined}>{count}</Badge>
                ) : null}
                {look.hint ? (
                  <span className="ml-auto min-w-0 truncate text-[10px] font-normal">
                    {t(look.hint)}
                  </span>
                ) : null}
              </button>
              {/* The block stays mounted while folded, hidden rather than removed:
                  it is what reports how much is in there, and a heading that goes
                  quiet when it is folded is the one time it has to speak. A block
                  with nothing to say draws nothing either way. */}
              <div className={cn("flex min-h-0 flex-col", !shown && "hidden")}>
                <Body />
              </div>
            </section>
          );
        })}
      </div>
    </aside>
  );
}
