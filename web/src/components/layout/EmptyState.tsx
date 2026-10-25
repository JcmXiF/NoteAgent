import type { ReactNode } from "react";

/**
 * A place that has nothing to show, and why.
 *
 * Used for emptiness and for failure alike, because to a reader they are the same
 * question — "what is here, and what can I do" — and the answer differs only in
 * whether there is a next step to offer. The action is where a retry goes.
 */
export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: ReactNode;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon}
      <p className="text-sm font-medium">{title}</p>
      {body ? <p className="max-w-sm text-xs text-muted-foreground">{body}</p> : null}
      {action ? <div className="mt-1.5 flex items-center gap-2">{action}</div> : null}
    </div>
  );
}
