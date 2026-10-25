import type * as React from "react";
import { cn } from "@/lib/utils";

const TONES = {
  neutral: "bg-muted text-muted-foreground",
  outline: "border border-border text-muted-foreground",
  primary: "bg-primary/10 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  destructive: "bg-destructive/10 text-destructive",
  cite: "bg-cite/15 text-cite",
  clip: "bg-clip/20 text-clip",
  remark: "bg-remark/15 text-remark",
} as const;

type BadgeTone = keyof typeof TONES;

function Badge({
  className,
  tone = "neutral",
  ...props
}: React.ComponentProps<"span"> & { tone?: BadgeTone }) {
  return (
    <span
      data-slot="badge"
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] leading-4 font-medium",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

export { Badge };
