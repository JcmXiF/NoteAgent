import { useMemo, useRef } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import { prepareMarkdown, REHYPE_PLUGINS, REMARK_PLUGINS, WIKI_SCHEME } from "@/lib/markdown";

/**
 * An answer, read as prose.
 *
 * Answers are written in the same markdown the notes are, and they name pages the
 * same way, so links are prepared and followed here too. The wording of a link
 * that leads nowhere is left to the caller: an answer is a place where a page may
 * be named before it exists, and saying so is the view's business.
 */
export function ChatMarkdown({
  body,
  onOpenLink,
}: {
  body: string;
  onOpenLink: (target: string) => void;
}) {
  const prepared = useMemo(() => prepareMarkdown(body), [body]);

  /**
   * What the answer's own links reach for, kept where the map below can find it.
   *
   * The map is built once — a map with a new identity makes React remount every
   * link it draws, so a link replaced under the reader's finger loses the click
   * aimed at it, and an answer that is still being written would rebuild itself
   * on every token — so it cannot close over this directly. It is written here on
   * every render, which is enough: the links are drawn later in the same pass,
   * and the ones that outlive it run after.
   */
  const handlers = useRef({ onOpenLink });
  handlers.current = { onOpenLink };

  const components = useMemo<Components>(
    () => ({
      a: ({ href, children, ...rest }) => {
        if (typeof href === "string" && href.startsWith(WIKI_SCHEME)) {
          const target = href.slice(WIKI_SCHEME.length);
          return (
            <a
              href={`#${target}`}
              className="cursor-pointer text-cite underline underline-offset-2 decoration-cite/40 hover:decoration-cite"
              onClick={(event) => {
                event.preventDefault();
                handlers.current.onOpenLink(target);
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
    }),
    [],
  );

  return (
    <div className={PROSE}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
        // The markdown renderer strips URL schemes it does not know, which would
        // quietly turn every page link into a link to nowhere.
        urlTransform={(url) => (url.startsWith(WIKI_SCHEME) ? url : defaultUrlTransform(url))}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
}

/** Tighter than a page's own prose: an answer sits inside a conversation. */
const PROSE = [
  "text-[13px] leading-relaxed",
  "[&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
  "[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_li]:my-0.5",
  // A page's own marks read here the same as they do in the editor: the two
  // readers of a page must not draw the same characters two ways.
  "[&_mark]:rounded-sm [&_mark]:bg-[color-mix(in_oklab,#facc15_45%,transparent)] [&_mark]:px-0.5",
  "[&_u]:underline [&_u]:underline-offset-2",
  "[&_sub]:align-sub [&_sub]:text-[0.75em] [&_sup]:align-super [&_sup]:text-[0.75em]",
  "[&_blockquote]:my-1.5 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
  "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[12px]",
  "[&_pre]:my-1.5 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_table]:my-1.5 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:px-2 [&_th]:py-1",
  "[&_td]:border [&_td]:px-2 [&_td]:py-1 [&_hr]:my-3 [&_img]:my-1.5 [&_img]:max-w-full",
  "[&_.katex-display]:my-2 [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden",
].join(" ");
