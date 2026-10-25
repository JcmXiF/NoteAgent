import { cn } from "@/lib/utils";
import { IconTip } from "@/components/ui/tooltip";

/**
 * Where a piece of work has got to — and where it can be taken back to.
 *
 * A job's steps are not a progress bar: each one is a step the work actually takes,
 * and the interesting part is the one being worked on. A step that can be taken
 * back is a button, because going back is a decision the reader makes rather than
 * something that happens to them.
 *
 * The deep search round under the graph is the one thing that reports itself this
 * way, so it is drawn from here rather than from the panel that holds it.
 */
type Step = {
  id: string;
  label: string;
  state: "pending" | "current" | "done" | "blocked";
};

export function StepBar({
  steps,
  onRewind,
  disabled = false,
  label,
  /** What going back does, said on each step that can be gone back to. */
  rewindHint,
}: {
  steps: Step[];
  /** Going back to a step. Omitted when the work can no longer be taken back. */
  onRewind?: (id: string) => void;
  /** True while something is running: nothing is taken back mid-run. */
  disabled?: boolean;
  /** Named for a reader who cannot see which track this is. */
  label: string;
  rewindHint?: (step: Step) => string;
}) {
  if (steps.length === 0) return null;
  return (
    <ol aria-label={label} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
      {steps.map((step, index) => {
        const back = onRewind && step.state === "done" && !disabled ? onRewind : null;
        const text = `${index + 1}. ${step.label}`;
        return (
          <li
            key={step.id}
            className={cn(
              "text-[11px]",
              step.state === "current" && "font-semibold text-foreground",
              step.state === "done" && "text-cite",
              step.state === "pending" && "text-muted-foreground",
              step.state === "blocked" && "text-muted-foreground opacity-70",
            )}
          >
            {back ? (
              <IconTip label={rewindHint ? rewindHint(step) : text}>
                <button
                  type="button"
                  className="rounded underline-offset-2 hover:underline"
                  onClick={() => back(step.id)}
                >
                  {text}
                </button>
              </IconTip>
            ) : (
              text
            )}
          </li>
        );
      })}
    </ol>
  );
}
