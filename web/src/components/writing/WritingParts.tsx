import type { ReactNode } from "react";

export function EmptyLine({ children }: { children: ReactNode }) {
  return (
    <p className="mt-2 text-xs text-muted-foreground">{children}</p>
  );
}

export function Card({
  title,
  count,
  className = "",
  children,
}: {
  title: string;
  count: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`rounded-md border bg-card p-3 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium">{title}</h2>
        <span className="text-[11px] text-muted-foreground">{count}</span>
      </div>
      {children}
    </section>
  );
}
