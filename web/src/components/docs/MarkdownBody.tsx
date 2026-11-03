import { createElement, useMemo, useRef, type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import {
  prepareMarkdown,
  REHYPE_PLUGINS,
  REMARK_PLUGINS,
  WIKI_SCHEME,
} from "@/lib/markdown";
import { cn } from "@/lib/utils";

/**
 * How a stretch of markdown is read, wherever it is shown.
 *
 * A page, a remark about one and a chat answer all have to look like the same app
 * wrote them, so the plugins, the link handling and the spacing live here rather
 * than in whichever component happens to draw first. What differs between them is
 * only what their own elements reach for — a page's headings can be landed on, a
 * remark's cannot — and that is what the options are for.
 */

/**
 * How the elements of markdown text are spaced.
 *
 * On the container, because these are the margins the flow needs and not the
 * margins of any one element. A caller adds its own padding to this rather than
 * picking the spacing again, which is what keeps two readings of the same
 * markdown from drifting apart.
 */
const MARKDOWN_FLOW = cn(
  "[&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_li]:my-0.5 [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
  "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[12px]",
  "[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:px-2 [&_th]:py-1 [&_td]:border [&_td]:px-2 [&_td]:py-1",
  "[&_hr]:my-4 [&_img]:my-2 [&_img]:max-w-full",
  // A display formula is wide by nature; it scrolls rather than stretching the
  // pane, and inline maths is allowed to break between terms.
  "[&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden",
);

/**
 * The box a stretch of markdown is read in.
 *
 * Shared for the same reason the reading itself is: the text has to look like the
 * same app wrote it wherever it appears, and two readers that each picked their
 * own spacing would disagree about how the same paragraph reads. A caller passes
 * the padding its own place needs and nothing else.
 */
export function MarkdownFlow({
  className,
  children,
}: {
  /** Room this particular reading needs around the text. */
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn("markdown-body text-[13px] leading-relaxed", MARKDOWN_FLOW, className)}
    >
      {children}
    </div>
  );
}

/** What a page's own elements reach for, beyond reading the text. */
type MarkdownHeadingHooks = {
  /** Told about each heading's node, so a section can be brought into view. */
  register?: (children: ReactNode) => (node: HTMLElement | null) => void;
  /** Drawn past a heading's text — the way to the material it came from, say. */
  extra?: (children: ReactNode) => ReactNode;
};

type MarkdownOptions = {
  /** Opens the page a `[[wiki link]]` names; the view decides what that means. */
  onOpenLink?: (target: string) => void;
  /** Whether that page exists, so a link that leads nowhere can say so first. */
  isKnownLink?: (target: string) => boolean;
  /** What to call a link whose page is not there. */
  missingTitle?: string;
  /** What a heading carries besides its text. */
  headings?: MarkdownHeadingHooks;
};

/**
 * One stretch of markdown, read as markdown is read everywhere in the app.
 *
 * Preparation happens per stretch rather than once for the whole text: rewriting
 * a wiki link to carry its target changes how much room the line takes up, so a
 * stretch cut by an offset measured before that would land in the wrong place.
 */
export function MarkdownBody({
  text,
  components,
}: {
  text: string;
  components?: Components;
}) {
  const prepared = useMemo(() => prepareMarkdown(text), [text]);
  if (!prepared) return null;
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
      components={components}
      urlTransform={urlTransform}
    >
      {prepared}
    </ReactMarkdown>
  );
}

/**
 * The element handlers markdown is read with, built once and reading their
 * options as they are used.
 *
 * Built once because a map with a new identity makes React remount every heading
 * it draws, and a heading replaced under the reader's finger loses the click
 * aimed at it. Reading the options as they are used is what lets the map be built
 * once while still reaching for the handlers of the moment.
 */
export function useMarkdownComponents(options: MarkdownOptions): Components {
  const latest = useRef(options);
  latest.current = options;
  return useMemo(() => markdownComponents(() => latest.current), []);
}

/** The element handlers, with what to do about a page's own headings. */
function markdownComponents(read: () => MarkdownOptions): Components {
  const heading = (level: "h1" | "h2" | "h3", className: string) =>
    function Heading({ children }: { children?: ReactNode }) {
      const { headings } = read();
      return createElement(
        level,
        { className, ref: headings?.register?.(children) },
        children,
        headings?.extra?.(children),
      );
    };

  return {
    a: ({ href, children, ...rest }) => {
      const { onOpenLink, isKnownLink, missingTitle } = read();
      // A link the wiki itself made: resolving the target is the view's job, so
      // it is not followed as an address.
      if (typeof href === "string" && href.startsWith(WIKI_SCHEME)) {
        const target = href.slice(WIKI_SCHEME.length);
        // A page that has not been written yet is marked as such rather than
        // looking like an ordinary link that happens to do nothing.
        const missing = isKnownLink ? !isKnownLink(target) : false;
        return (
          <a
            href={`#${target}`}
            title={missing ? missingTitle : undefined}
            className={cn(
              "cursor-pointer underline underline-offset-2",
              missing
                ? "text-muted-foreground decoration-dashed decoration-muted-foreground/60"
                : "text-cite decoration-cite/40 hover:decoration-cite",
            )}
            onClick={(event) => {
              event.preventDefault();
              onOpenLink?.(target);
            }}
          >
            {children}
          </a>
        );
      }
      return (
        <a
          className="text-cite underline underline-offset-2"
          href={href}
          target="_blank"
          rel="noreferrer"
          {...rest}
        >
          {children}
        </a>
      );
    },
    // Markdown's own headings start a level down from the view's, so a page and
    // the frame around it do not look like two titles of the same rank.
    h1: heading("h1", "mt-4 mb-2 text-base font-semibold"),
    h2: heading("h2", "mt-4 mb-2 text-sm font-semibold"),
    h3: heading("h3", "mt-3 mb-1.5 text-[13px] font-semibold"),
  };
}

/**
 * Markdown readers strip URL schemes they do not recognise, which would quietly
 * turn every wiki link into a link to nowhere. Ours is let through; everything
 * else is still sanitised as usual.
 */
function urlTransform(url: string): string {
  return url.startsWith(WIKI_SCHEME) ? url : defaultUrlTransform(url);
}
