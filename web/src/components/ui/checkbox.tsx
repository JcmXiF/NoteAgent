import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A box that is either ticked or not.
 *
 * Behind this wrapper so every tick in the app looks the same, and so a caller
 * reads it like the other controls. The tick itself is drawn rather than left to
 * the platform, which would ignore the theme's colours.
 */
export function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border border-input",
        "transition-colors outline-none",
        "data-checked:border-cite data-checked:bg-cite/15 data-checked:text-cite",
        "hover:border-foreground/40",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center">
        <Check className="size-2.5" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
