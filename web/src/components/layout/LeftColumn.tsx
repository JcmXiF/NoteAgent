import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { ClearFinishedButton, JobsList, JobsSummary } from "@/components/layout/JobsList";
import { ProjectBar } from "@/components/layout/ProjectBar";
import { moduleViews } from "@/components/module-views";
import { beginPanelDrag } from "@/lib/drag";
import { cn } from "@/lib/utils";
import { MODULE_META, type ModuleId } from "@/modules";
import {
  growingLeftSection,
  leftSectionHeight,
  leftSectionOpen,
  useActivePaneSlot,
  useLayoutStore,
  type LeftSectionId,
} from "@/stores/layout-store";

/**
 * The sidebar, as the sections it is made of.
 *
 * A column that holds one fixed stack has to guess what the reader wants: what the
 * module is (a list of conversations, a file tree) and what the app is doing are
 * different things with different claims on the room. Here they are peers — each
 * folds, each can be moved, and the one that does not grow can be dragged to the
 * height the reader wants.
 */
export function LeftColumn() {
  const { t } = useTranslation();
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  // The sidebar follows whichever pane has focus.
  const activeSlot = useActivePaneSlot();
  const activeModule = activeSlot === "side" && side ? side : view;
  const sections = useLayoutStore((state) => state.leftSections);

  const nameOf = (section: LeftSectionId) => sectionName(section, activeModule, t);

  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar">
      <ProjectBar />
      {sections.order.map((id, index) => (
        <Fragment key={id}>
          {index > 0 ? (
            <SectionGrip above={sections.order[index - 1]} below={id} nameOf={nameOf} />
          ) : null}
          <Section
            id={id}
            nameOf={nameOf}
            module={activeModule}
            first={index === 0}
            last={index === sections.order.length - 1}
          />
        </Fragment>
      ))}
    </div>
  );
}

/** What a section is called, wherever it needs naming. */
function sectionName(
  section: LeftSectionId,
  module: ModuleId,
  t: (key: string) => string,
): string {
  return section === "module" ? t(`${MODULE_META[module].i18nKey}.sidebar`) : t("activity.title");
}

function Section({
  id,
  module,
  nameOf,
  first,
  last,
}: {
  id: LeftSectionId;
  module: ModuleId;
  nameOf: (section: LeftSectionId) => string;
  first: boolean;
  last: boolean;
}) {
  const sections = useLayoutStore((state) => state.leftSections);
  const open = leftSectionOpen(sections, id);
  // One section takes the room the others did not; folding it away hands that room
  // to whatever is left open rather than leaving a gap.
  const grows = growingLeftSection(sections) === id;

  return (
    <section
      className={cn("flex min-h-0 flex-col", grows ? "flex-1" : "shrink-0")}
      style={grows ? undefined : { height: open ? leftSectionHeight(sections, id) : undefined }}
    >
      <SectionHeader id={id} name={nameOf(id)} module={module} open={open} first={first} last={last} />
      {open ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <SectionBody id={id} module={module} />
        </div>
      ) : null}
    </section>
  );
}

/** The bar that names a section and carries what can be done to it. */
function SectionHeader({
  id,
  name,
  module,
  open,
  first,
  last,
}: {
  id: LeftSectionId;
  name: string;
  module: ModuleId;
  open: boolean;
  first: boolean;
  last: boolean;
}) {
  const { t } = useTranslation();
  const setLeftSectionOpen = useLayoutStore((state) => state.setLeftSectionOpen);
  const move = useLayoutStore((state) => state.moveLeftSection);
  const meta = MODULE_META[module];

  return (
    <header className="group flex h-8 shrink-0 items-center gap-1 border-b px-1.5">
      <button
        type="button"
        aria-expanded={open}
        aria-label={name}
        title={name}
        onClick={() => setLeftSectionOpen(id, !open)}
        className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
      >
        {id === "module" ? (
          <>
            <meta.icon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              {name}
            </span>
          </>
        ) : (
          // Running work says what it is doing in its own line, which is more use
          // here than the word "jobs" would be; the name stays on the control.
          <JobsSummary />
        )}
      </button>

      {/* Reordering is by buttons rather than by dragging the bar: it is reachable
          from the keyboard, and with two sections there is nothing to hunt for. */}
      <span className="flex shrink-0 items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100">
        {id === "jobs" ? <ClearFinishedButton /> : null}
        <IconTip label={t("nav.moveUp")}>
          <Button variant="ghost" size="icon-xs" disabled={first} onClick={() => move(id, -1)}>
            <ChevronUp />
          </Button>
        </IconTip>
        <IconTip label={t("nav.moveDown")}>
          <Button variant="ghost" size="icon-xs" disabled={last} onClick={() => move(id, 1)}>
            <ChevronDown />
          </Button>
        </IconTip>
      </span>
    </header>
  );
}

function SectionBody({ id, module }: { id: LeftSectionId; module: ModuleId }) {
  if (id === "jobs") return <JobsList />;
  const { Sidebar } = moduleViews(module);
  return <Sidebar />;
}

/**
 * The grip between two sections, which sets the height of whichever of them does
 * not grow.
 *
 * Only one section grows, so with two of them this is unambiguous: the drag moves
 * the boundary. It appears only while both are open, because a folded section has
 * no height to argue about.
 */
function SectionGrip({
  above,
  below,
  nameOf,
}: {
  above: LeftSectionId;
  below: LeftSectionId;
  nameOf: (section: LeftSectionId) => string;
}) {
  const { t } = useTranslation();
  const sections = useLayoutStore((state) => state.leftSections);
  const setLeftSectionHeight = useLayoutStore((state) => state.setLeftSectionHeight);
  // The one that does not grow takes the drag; the growing one takes the rest.
  const grower = growingLeftSection(sections);
  const target = above === grower ? below : above;
  const open = leftSectionOpen(sections, above) && leftSectionOpen(sections, below);
  if (!grower || target === grower || !open) return null;

  const start = leftSectionHeight(sections, target);
  const sign = above === target ? 1 : -1;

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={t("nav.resizeSection", { section: nameOf(target) })}
      className="h-1 shrink-0 cursor-row-resize bg-border/40 hover:bg-primary/30 active:bg-primary/40"
      onMouseDown={(event) => {
        const from = event.clientY;
        beginPanelDrag(event, {
          // The store clamps, so an overshoot simply stops at the limit and the
          // boundary comes back with the pointer.
          onMove: (moveEvent) =>
            setLeftSectionHeight(target, start + sign * (moveEvent.clientY - from)),
        });
      }}
    />
  );
}
