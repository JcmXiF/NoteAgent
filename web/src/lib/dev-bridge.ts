import { useActivityStore } from "@/stores/activity-store";
import { useAcquireStore } from "@/stores/acquire-store";
import { useBrowseStore } from "@/stores/browse-store";
import { useClipsStore } from "@/stores/clips-store";
import { useChatStore } from "@/stores/chat-store";
import { useCompileStore } from "@/stores/compile-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useDocsReads } from "@/stores/docs-reads";
import { useDocsStore } from "@/stores/docs-store";
import { useGraphStore } from "@/stores/graph-store";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotePageStore } from "@/stores/note-page-store";
import { useNotesStore } from "@/stores/notes-store";
import { usePlanStore } from "@/stores/plan-store";
import { useReadingStore } from "@/stores/reading-store";
import { useUiStore } from "@/stores/ui-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import * as api from "@/lib/api";
import * as capture from "@/lib/capture";
import * as clips from "@/lib/clips";
import * as deepSearch from "@/lib/deep-search";
import * as desktop from "@/lib/desktop";
import * as devCounters from "@/lib/dev-counters";
import * as readingHistory from "@/lib/reading-history";
import * as message from "@/lib/message";
import * as noteAnchor from "@/lib/note-anchor";
import * as noteDraft from "@/lib/note-draft";
import * as noteList from "@/lib/note-list";
import * as pageBody from "@/lib/page-body";
import * as sourceKind from "@/lib/source-kind";
import * as wikiEvidence from "@/lib/wiki-evidence";
import * as wikiForce from "@/lib/wiki-force";
import * as wikiGraph from "@/lib/wiki-graph";
import * as wikiLocators from "@/lib/wiki-locators";

/**
 * Development-only bridge for inspecting stores, calling the API and exercising
 * the pure helpers from the browser console. main.tsx imports this dynamically
 * so it never reaches a production bundle.
 */
export function installDevBridge(): void {
  Object.defineProperty(window, "__noteagent", {
    value: {
      stores: {
        activity: useActivityStore,
        acquire: useAcquireStore,
        browse: useBrowseStore,
        clips: useClipsStore,
        compile: useCompileStore,
        chat: useChatStore,
        deepSearch: useDeepSearchStore,
        docs: useDocsStore,
        docsReads: useDocsReads,
        graph: useGraphStore,
        layout: useLayoutStore,
        nav: useNavStore,
        notePage: useNotePageStore,
        notes: useNotesStore,
        plan: usePlanStore,
        reading: useReadingStore,
        ui: useUiStore,
        workspace: useWorkspaceStore,
      },
      api,
      lib: {
        capture,
        clips,
        message,
        sourceKind,
        wikiEvidence,
        wikiLocators,
        wikiGraph,
        wikiForce,
        deepSearch,
        desktop,
        readingHistory,
        noteAnchor,
        noteDraft,
        noteList,
        pageBody,
      },
      /** How often each view has rendered, for checking that an animation does not re-render. */
      renderCounts: devCounters.renderCounts,
      resetRenderCounts: devCounters.resetRenderCounts,
    },
    configurable: true,
  });
}
