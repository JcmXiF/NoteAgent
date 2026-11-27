# Changelog

Every release has one section. The release workflow copies the section matching
the app version into the GitHub Release body, so a version without a section
cannot be published.

## v0.2.0

- Writing is now an agent loop over one project-level writing state: the agent
  searches, selects pages, frames passages, composes sections and publishes with
  `write_wiki`; the old plan/confirm/compile pipeline is retired.
- A publish now maintains `wiki/index.md` and `wiki/log.md`, and `wiki_read` can
  page through long pages instead of stopping at 8,000 characters.
- Retrieval is optional and configured in the app: a local hybrid index
  (BM25 + vectors + RRF) behind `search`, with a settings screen that shows and
  rebuilds the index.
- Interrupted agent runs can be resumed or abandoned; page writes are recorded
  in an effect journal with page history and restore.
- Skills are discovered from project, user and bundled roots and loaded on
  demand; the product prompt is versioned and migrated for older sessions.
- The V4 quality eval replaces V3 and stays local-only; deterministic behaviour
  evals still run with `python -m evals`.

## v0.1.2

- The chat loop is replaced by a general agent harness: each session runs a
  tool-calling agent that proposes pages and plans for you to approve, and can
  be stopped and continued later.
- Sessions are stored one JSONL per conversation, keep the whole transcript
  even after compaction, and are listed again after a restart.
- Chats from earlier versions are migrated to the new session store on first
  launch.
- The packaged app carries the new core and reports its version through the
  same health check.

## v0.1.1

- The app checks for a newer release shortly after it starts and every six
  hours. Help → Check for updates… runs the check on demand, and About shows
  the running version.
- When a new release exists, a native notification shows its version and a
  short summary; clicking it opens the GitHub release page. The app never
  downloads or installs an update itself.
- Release notes come from this changelog, and the Windows build stamps the
  core version from the app version.

## v0.1.0

First release.

- Collect sources from web pages, PDFs, Office documents, images and Markdown,
  and keep only the passages you select.
- Compile selected passages into a persistent Markdown wiki; pages link back to
  the exact source passages they came from.
- The program proposes pages and the human decides what gets written.
- Run the core and the interface in one Windows desktop app, with a built-in
  guest browser for signing in and keeping pages. No separate Python or Node
  installation is needed.
