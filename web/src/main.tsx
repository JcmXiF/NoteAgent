import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Importing ui-store applies the stored theme and text size to the document, so
// it has to happen before the first render to avoid a flash of the default theme.
import { watchSystemTheme } from "@/lib/theme";
import "@/stores/ui-store";
import { startClipsSync } from "@/stores/clips-store";
import { startCompileSync } from "@/stores/compile-store";
import { startAcquireSync } from "@/stores/acquire-store";
import { startBrowseSync } from "@/stores/browse-store";
import { startChatSync } from "@/stores/chat-store";
import { startNotePageSync } from "@/stores/note-page-store";
import { startDeepSearchSync } from "@/stores/deep-search-store";
import { startDocsSync } from "@/stores/docs-store";
import { startGraphSync } from "@/stores/graph-store";
import { startReadingSync } from "@/stores/reading-store";
import "@/i18n";
import App from "@/App";
import "@/index.css";
// Math is rendered by the shared markdown pipeline, whose rules live in
// lib/markdown.ts; its stylesheet belongs with the app's global styles.
import "katex/dist/katex.min.css";

watchSystemTheme();
// The workbench list and the selections both follow the active project; wired
// here so no view has to own it.
startClipsSync();
startCompileSync();
startAcquireSync();
startChatSync();
startNotePageSync();
startBrowseSync();
startDocsSync();
startGraphSync();
startDeepSearchSync();
startReadingSync();

if (import.meta.env.DEV) {
  void import("@/lib/dev-bridge").then((mod) => mod.installDevBridge());
}

const host = document.getElementById("root");
if (!host) {
  throw new Error("Missing #root mount point");
}

createRoot(host).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
