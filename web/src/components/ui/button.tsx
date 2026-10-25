import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { cn } from "@/lib/utils";

/**
 * Compact control sizes (h-7 / h-8) suit a dense desktop layout. The variant
 * tables are plain objects rather than cva, which keeps this file readable.
 */
const VARIANTS = {
  default: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline:
    "border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
  secondary:
    "bg-secondary text-secondary-foreground hover:bg-secondary/80 aria-expanded:bg-secondary",
  ghost: "hover:bg-muted hover:text-foreground aria-expanded:bg-muted dark:hover:bg-muted/50",
  destructive:
    "bg-destructive/10 text-destructive hover:bg-destructive/20 focus-visible:border-destructive/40 focus-visible:ring-destructive/20",
  link: "text-primary underline-offset-4 hover:underline",
} as const;

const SIZES = {
  sm: "h-7 gap-1 px-2.5 text-[0.8rem]",
  default: "h-8 gap-1.5 px-2.5 text-sm",
  lg: "h-9 gap-1.5 px-2.5 text-sm",
  icon: "size-8",
  "icon-sm": "size-7",
  "icon-xs": "size-6",
} as const;

type ButtonVariant = keyof typeof VARIANTS;
type ButtonSize = keyof typeof SIZES;

function buttonClass(
  variant: ButtonVariant = "default",
  size: ButtonSize = "default",
): string {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent whitespace-nowrap font-medium transition-all outline-none select-none",
    "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
    "disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
    VARIANTS[variant],
    SIZES[size],
  );
}

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <ButtonPrimitive
      data-slot="button"
      // base-ui accepts either a string or a (state) => string, so handle both.
      className={(state) =>
        cn(
          buttonClass(variant, size),
          typeof className === "function" ? className(state) : className,
        )
      }
      {...props}
    />
  );
}

export { Button };
