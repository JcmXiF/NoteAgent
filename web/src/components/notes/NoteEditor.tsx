import { basicSetup, EditorView } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/**
 * The look of the writing surface.
 *
 * A remark is prose, so the editor's own code palette is not the right one: its
 * colours were chosen for a light background and would be unreadable on the other.
 * The marks keep the part that says what kind of text they are — a heading's
 * weight, a link's underline, a quote's slant — and take their colour from the
 * text around them, so a remark reads the same whichever theme is on. The gutter
 * is hidden for the same reason: line numbers and fold arrows are for reading
 * code, and a remark is a paragraph.
 */
const noteTheme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "13px",
    backgroundColor: "transparent",
    color: "inherit",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "var(--font-sans-stack)",
    lineHeight: "1.7",
    overflow: "auto",
  },
  ".cm-content": { padding: "8px 12px", color: "inherit", caretColor: "var(--foreground)" },
  ".cm-line span": { color: "inherit" },
  ".cm-gutters": { display: "none" },
  ".cm-activeLine": {
    backgroundColor: "color-mix(in oklab, var(--foreground) 5%, transparent)",
  },
  ".cm-selectionBackground": {
    backgroundColor: "color-mix(in oklab, var(--cite) 22%, transparent)",
  },
  "&.cm-focused .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklab, var(--cite) 32%, transparent)",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--foreground)" },
});

type NoteEditorProps = {
  /** What is written. */
  value: string;
  /** Told about each change, so the screen holds what is being written. */
  onChange: (value: string) => void;
  /**
   * Told when the writer's attention leaves the editor entirely.
   *
   * Moving between the editor and something else on the same screen counts;
   * moving between parts of the editor itself does not, since that is still
   * writing.
   */
  onBlur?: () => void;
  /** What the editor is called, for someone who cannot see the pane around it. */
  label: string;
  /** What to show while nothing has been written. */
  placeholder?: string;
  /** Whether to put the caret in it as soon as it appears. */
  autoFocus?: boolean;
  className?: string;
};

/**
 * The surface a remark is written on.
 *
 * Plain markdown in a real text editor: undo, bracket closing, line wrapping at
 * the window's edge. Nothing is layered on top of it — no hidden marks, no
 * source-and-preview swapping, no handle in the margin — because a remark is not
 * a wiki page: what is written here is the whole of what is stored, so the text
 * on screen is the text, and nothing has to be worked out about it.
 *
 * The editor owns its own buffer rather than being rendered from React, and text
 * arriving from outside is put in only when it differs from what is there:
 * re-setting the buffer on every render would take the caret away from the writer
 * mid-sentence.
 */
export function NoteEditor({
  value,
  onChange,
  onBlur,
  label,
  placeholder,
  autoFocus,
  className,
}: NoteEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // Read by handlers the editor keeps for its whole life, so they do not have to
  // be rebuilt — and the caret does not have to be given up — when they change.
  const latest = useRef({ onChange, onBlur });
  latest.current = { onChange, onBlur };
  // Set while text from outside is being put in, so the editor does not report it
  // back as something the writer did.
  const placing = useRef(false);

  useEffect(() => {
    const parent = host.current;
    if (!parent) return undefined;
    const created = new EditorView({
      doc: value,
      parent,
      extensions: [
        basicSetup,
        markdown(),
        EditorView.lineWrapping,
        noteTheme,
        EditorView.updateListener.of((update) => {
          if (!update.docChanged || placing.current) return;
          latest.current.onChange(update.state.doc.toString());
        }),
      ],
    });
    view.current = created;
    if (autoFocus) created.focus();
    return () => {
      view.current = null;
      created.destroy();
    };
    // The editor is built once and then keeps its own buffer; what it holds is
    // settled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Something written elsewhere — another remark's draft — replaces what is here.
  // What the editor itself just produced is already here, so it is left alone.
  useEffect(() => {
    const current = view.current;
    if (!current) return;
    if (value === current.state.doc.toString()) return;
    placing.current = true;
    current.dispatch({
      changes: { from: 0, to: current.state.doc.length, insert: value },
    });
    placing.current = false;
  }, [value]);

  useEffect(() => {
    view.current?.contentDOM.setAttribute("aria-label", label);
  }, [label]);

  return (
    <div
      className={cn("relative min-h-0", className)}
      // Focus events bubble, so this catches the writer leaving the editor for
      // anything else on the screen. Moving inside the editor — into its own
      // fields, say — is not leaving it.
      onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget)) return;
        onBlur?.();
      }}
    >
      <div ref={host} className="h-full" />
      {placeholder && value === "" ? (
        <span className="pointer-events-none absolute top-2 left-3 text-[13px] text-muted-foreground">
          {placeholder}
        </span>
      ) : null}
    </div>
  );
}
