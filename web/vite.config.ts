import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// vitest/config re-exports Vite's defineConfig with the `test` field typed.
import { defineConfig } from "vitest/config";

const srcDir = path.resolve(import.meta.dirname, "./src");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": srcDir,
    },
  },
  server: {
    host: "127.0.0.1",
    // Fixed rather than left to Vite: the core accepts a page served from this
    // port as one of its own (see `noteagent/api/guard.py`), and the shell loads
    // the address once, so a server that moved to another port would not be found.
    port: 5174,
    strictPort: true,
    proxy: {
      // Ingest, planning and fetching can run for minutes; keep the proxy from
      // cutting them off.
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  test: {
    // Logic runs without a document by default; a file that needs a real one
    // declares `// @vitest-environment happy-dom` at the top (the editor tests
    // do — an editor is built against a document).
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
