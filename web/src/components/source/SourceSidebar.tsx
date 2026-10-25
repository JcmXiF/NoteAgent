import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileUp, Loader2, Link, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IconTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  MAX_LOCAL_BYTES,
  MAX_LOCAL_FILES,
  type LocalFilePayload,
} from "@/lib/api/acquire";
import { fidelityLabel, fidelityTone, isHttpUrl } from "@/lib/capture";
import { writtenPathsFrom } from "@/lib/acquire-index";
import type { CaptureEntry } from "@/lib/acquire-index";
import { useAcquireStore } from "@/stores/acquire-store";
import { useNavStore } from "@/stores/nav-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { RemoveSourceDialog } from "@/components/source/RemoveSourceDialog";

/** Reads a file into the base64 payload the backend expects. */
async function toPayload(file: File): Promise<LocalFilePayload> {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  // Chunked so a large file does not blow the argument limit of fromCharCode.
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return {
    name: file.name,
    mediaType: file.type,
    contentBase64: btoa(binary),
  };
}

/**
 * The site a source came from, when it has a URL.
 *
 * A page's title says what it is; the host says where it came from, which is
 * what tells two similarly titled pages apart.
 */
function siteOf(entry: CaptureEntry): string {
  const raw = entry.capture?.finalUrl || entry.url;
  if (!/^https?:/i.test(raw)) return "";
  try {
    return new URL(raw).hostname;
  } catch {
    return "";
  }
}

/**
 * The workbench list, and the ways to add a source.
 *
 * Adding by link and adding local files are the two entry points that do not
 * need another view; the third is the built-in browser, which hands a page over
 * as it is kept.
 */
export function SourceSidebar() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const entries = useAcquireStore((state) => state.entries);
  const focusedUrl = useAcquireStore((state) => state.focusedUrl);
  const busy = useAcquireStore((state) => state.busy);
  const error = useAcquireStore((state) => state.error);
  const setError = useAcquireStore((state) => state.setError);
  const setFocusedUrl = useAcquireStore((state) => state.setFocusedUrl);
  const fetchUrl = useAcquireStore((state) => state.fetchUrl);
  const addLocalFiles = useAcquireStore((state) => state.addLocalFiles);

  // Derived state is computed here rather than in a selector: a selector that
  // returns a fresh object would re-render on every store change.
  const savedPaths = useMemo(() => writtenPathsFrom(entries), [entries]);

  const go = useNavStore((state) => state.go);

  const [link, setLink] = useState("");
  const [dropping, setDropping] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  /** The sources whose removal is being confirmed, or null. */
  const [removing, setRemoving] = useState<string[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const submitLink = async () => {
    const href = link.trim();
    if (!href) return;
    if (!project) {
      // Kept in the box: a link typed into a screen that cannot use it should still
      // be there once there is a project to put it in.
      setError(t("project.needed"));
      return;
    }
    if (!isHttpUrl(href)) {
      setError(t("sources.paste.notHttp"));
      return;
    }
    setLink("");
    await fetchUrl(href);
  };

  const takeFiles = async (list: File[]) => {
    if (list.length === 0) return;
    const unique = list.slice(0, MAX_LOCAL_FILES);
    if (list.length > MAX_LOCAL_FILES) {
      setError(t("sources.local.tooMany", { count: MAX_LOCAL_FILES }));
    }
    const oversized = unique.find((file) => file.size > MAX_LOCAL_BYTES);
    if (oversized) {
      setError(t("sources.local.tooLarge", { name: oversized.name }));
      return;
    }
    try {
      await addLocalFiles(await Promise.all(unique.map(toPayload)));
    } catch {
      setError(t("sources.local.unreadable", { name: unique[0]?.name ?? "" }));
    }
  };

  const toggle = (url: string) =>
    setSelected((prev) =>
      prev.includes(url) ? prev.filter((item) => item !== url) : [...prev, url],
    );

  const removeSelected = () => {
    if (selected.length === 0) return;
    setRemoving(selected);
  };

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      onDragOver={(event) => {
        event.preventDefault();
        setDropping(true);
      }}
      onDragLeave={() => setDropping(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDropping(false);
        void takeFiles([...event.dataTransfer.files]);
      }}
    >
      <div className="flex shrink-0 flex-col gap-1.5 border-b p-2">
        {!project ? (
          <p className="rounded-md bg-muted/60 px-2 py-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("project.needed")}
          </p>
        ) : null}
        <form
          className="flex items-center gap-1"
          onSubmit={(event) => {
            event.preventDefault();
            void submitLink();
          }}
        >
          <Input
            className="h-7 flex-1 text-xs"
            value={link}
            placeholder={t("sources.paste.placeholder")}
            aria-label={t("sources.paste.label")}
            disabled={!project}
            onChange={(event) => setLink(event.target.value)}
          />
          <Button
            type="submit"
            size="icon-sm"
            variant="outline"
            disabled={busy || !project || !link.trim()}
            aria-label={t("sources.paste.submit")}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Link />}
          </Button>
        </form>

        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            disabled={busy || !project}
            onClick={() => fileInputRef.current?.click()}
          >
            <FileUp /> {t("sources.local.choose")}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            disabled={!project}
            accept=".html,.htm,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.svg,.avif"
            onChange={(event) => {
              void takeFiles([...(event.target.files ?? [])]);
              event.target.value = "";
            }}
          />
          {selected.length > 0 ? (
            <IconTip label={t("sources.remove.selected", { count: selected.length })}>
              <Button variant="ghost" size="icon-sm" onClick={removeSelected}>
                <Trash2 />
              </Button>
            </IconTip>
          ) : null}
        </div>

        {error ? (
          <button
            type="button"
            className="text-left text-[11px] text-destructive"
            onClick={() => setError(null)}
          >
            {error}
          </button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {dropping ? (
          <p className="m-2 rounded-md border border-dashed border-primary/60 p-3 text-center text-[11px] text-primary">
            {t("sources.local.dropHere")}
          </p>
        ) : null}

        {entries.length === 0 && !dropping ? (
          <div className="flex flex-col gap-1.5 p-3">
            <p className="text-xs font-medium">{t("sources.empty.title")}</p>
            <p className="text-[11px] text-muted-foreground">{t("sources.empty.body")}</p>
            <p className="text-[11px] text-muted-foreground/80">
              {t("sources.local.hint")}
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-px py-1">
            {entries.map((entry) => {
              const active = entry.url === focusedUrl;
              const path = savedPaths[entry.url];
              return (
                <li key={entry.url}>
                  <div
                    className={cn(
                      "group flex items-start gap-1.5 px-2 py-1.5 text-[11px]",
                      active ? "bg-accent" : "hover:bg-accent/40",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 size-3 shrink-0"
                      checked={selected.includes(entry.url)}
                      aria-label={entry.title}
                      onChange={() => toggle(entry.url)}
                    />
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => {
                        setFocusedUrl(entry.url);
                        go({ kind: "sources", sourceKey: entry.url }, { placement: "beside" });
                      }}
                    >
                      <span className="line-clamp-2 font-medium">{entry.title}</span>
                      {siteOf(entry) ? (
                        <span className="block truncate text-[10px] text-muted-foreground">
                          {siteOf(entry)}
                        </span>
                      ) : null}
                      <span className="mt-0.5 flex flex-wrap items-center gap-1">
                        <Badge tone={fidelityTone(entry.fidelity)}>
                          {t(fidelityLabel(entry.fidelity).key)}
                        </Badge>
                        {entry.kind ? <Badge tone="outline">{entry.kind}</Badge> : null}
                        {path ? (
                          <Badge tone="primary">{t("sources.saved")}</Badge>
                        ) : null}
                      </span>
                    </button>
                    <IconTip label={t("sources.remove.one")}>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="opacity-0 group-hover:opacity-100"
                        onClick={() => setRemoving([entry.url])}
                      >
                        <Trash2 />
                      </Button>
                    </IconTip>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <RemoveSourceDialog
        urls={removing}
        onClose={() => setRemoving(null)}
        onRemoved={() => setSelected([])}
      />
    </div>
  );
}
