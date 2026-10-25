import type { LucideIcon } from "lucide-react";
import {
  FileText,
  FolderOpen,
  Globe,
  MessageSquare,
  Network,
  StickyNote,
} from "lucide-react";

/**
 * The module registry — the single source of truth for the icon rail and for
 * which view a pane shows. Labels live in the i18n catalogs, so this file only
 * carries the keys and the presentation facts.
 */
export const MODULE_IDS = ["chat", "sources", "docs", "notes", "graph", "browse"] as const;

export type ModuleId = (typeof MODULE_IDS)[number];

type ModuleMeta = {
  id: ModuleId;
  /** Base i18n key; labels are read as `${i18nKey}.label`, `.hint`, `.sidebar`. */
  i18nKey: string;
  icon: LucideIcon;
  /**
   * Whether the window can only show one of it.
   *
   * The shell keeps one browser per window — one page area, one tab strip — so a
   * second pane showing it would be a frame with nothing in it.
   */
  single?: true;
};

export const MODULE_META: Record<ModuleId, ModuleMeta> = {
  chat: { id: "chat", i18nKey: "module.chat", icon: MessageSquare },
  sources: { id: "sources", i18nKey: "module.sources", icon: FolderOpen },
  docs: { id: "docs", i18nKey: "module.docs", icon: FileText },
  notes: { id: "notes", i18nKey: "module.notes", icon: StickyNote },
  graph: { id: "graph", i18nKey: "module.graph", icon: Network },
  browse: { id: "browse", i18nKey: "module.browse", icon: Globe, single: true },
};

export const MODULE_LIST: ModuleMeta[] = MODULE_IDS.map((id) => MODULE_META[id]);

/** Whether only one pane can be showing this module. */
export function isSingleModule(id: ModuleId): boolean {
  return MODULE_META[id].single === true;
}

export function isModuleId(value: unknown): value is ModuleId {
  return typeof value === "string" && (MODULE_IDS as readonly string[]).includes(value);
}
