/**
 * Which component draws each module.
 *
 * This is the one place a view is attached to a module: adding one means adding
 * an entry here, not editing the shell. Modules without an entry yet fall back
 * to a placeholder, so the app stays coherent while they are built.
 */
import type { ComponentType } from "react";
import type { ModuleId } from "@/modules";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatView } from "@/components/chat/ChatView";
import { BrowseSearch } from "@/components/browse/BrowseSearch";
import { BrowseView } from "@/components/browse/BrowseView";
import { SourceSidebar } from "@/components/source/SourceSidebar";
import { SourceView } from "@/components/source/SourceView";
import { DocsSidebar } from "@/components/docs/DocsSidebar";
import { DocsView } from "@/components/docs/DocsView";
import { NotesSidebar } from "@/components/notes/NotesSidebar";
import { NotesView } from "@/components/notes/NotesView";
import { GraphSidebar } from "@/components/graph/GraphSidebar";
import { GraphView } from "@/components/graph/GraphView";
import { NotAvailable } from "@/components/layout/NotAvailable";
import type { PaneSlot } from "@/stores/layout-store";

/**
 * The main area of a module, told which pane it is in.
 *
 * A view that keeps its own state needs to know: the reader does, so that the two
 * panes can read different pages. Views that do not care simply ignore it.
 */
type ModuleContent = ComponentType<{ slot: PaneSlot }>;

type ModuleViews = {
  /** The module's own panel in the sidebar. */
  Sidebar: ComponentType;
  /** The module's main area. */
  Content: ModuleContent;
};

const VIEWS: Partial<Record<ModuleId, ModuleViews>> = {
  chat: { Sidebar: ChatSidebar, Content: ChatView },
  sources: { Sidebar: SourceSidebar, Content: SourceView },
  docs: { Sidebar: DocsSidebar, Content: DocsView },
  notes: { Sidebar: NotesSidebar, Content: NotesView },
  browse: { Sidebar: BrowseSearch, Content: BrowseView },
  graph: { Sidebar: GraphSidebar, Content: GraphView },
};

export function moduleViews(id: ModuleId): ModuleViews {
  return VIEWS[id] ?? { Sidebar: NotAvailable, Content: NotAvailable };
}
