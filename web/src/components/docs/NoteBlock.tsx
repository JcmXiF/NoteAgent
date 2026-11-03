import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type NoteBlockProps = {
  /** The remark's own text, read the same way the page around it is read. */
  children: ReactNode;
  /** What the stripe and the label say this is. */
  label: string;
  /**
   * Opens the remark where it is written. The page it is drawn in is read-only,
   * so this is the only thing there is to do with it here.
   */
  onOpen?: () => void;
  /** What opening it is called, for someone who cannot see the pointer. */
  openLabel?: string;
};

/**
 * A remark, read beside the page it was written about.
 *
 * Marked out by a stripe and a label rather than boxed in: it belongs to the page
 * being read, and a frame around it would make it look like something interrupting
 * the text rather than something added to it.
 *
 * Read-only. A remark is written on its own screen, where the page it is about can
 * be seen alongside, so there is nothing to do here but make it legible and
 * unmistakeable for what it is — and, when that screen can be reached from here,
 * open it on this very remark.
 */
export function NoteBlock({ children, label, onOpen, openLabel }: NoteBlockProps) {
  const body = (
    <>
      <Badge tone="remark" className="mb-1">
        {label}
      </Badge>
      <div className="text-[13px] leading-relaxed">{children}</div>
    </>
  );

  const shell = "my-3 border-l-2 border-remark/50 bg-remark/5 py-1.5 pr-3 pl-3";
  if (!onOpen) return <aside className={shell}>{body}</aside>;

  /**
   * A remark's own links stay links.
   *
   * The whole remark opens the screen it is written on, and a link inside it
   * would otherwise do both — the reader asked to follow the link.
   */
  const clicked = (event: MouseEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest("a")) return;
    onOpen();
  };

  const keyed = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onOpen();
  };

  return (
    <aside
      role="button"
      tabIndex={0}
      aria-label={openLabel ?? label}
      className={cn(shell, "cursor-pointer hover:bg-remark/10")}
      onClick={clicked}
      onKeyDown={keyed}
    >
      {body}
    </aside>
  );
}
