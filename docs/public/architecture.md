# Architecture

NOTEAGENT turns sources a person selected into a Markdown wiki. Three programs take part, and one
of them — the Python core — owns every decision about what gets written.

## The three parts

| Part | Built with | Its job |
|------|------------|---------|
| Core (`src/noteagent/`) | Python, FastAPI | Fetch, extract, write, and serve. Owns the agent run and the project's writing state; serves the HTTP API, and in a packaged app also serves the interface. |
| Interface (`web/`) | Vite, React, TypeScript | Arrange what the core returns, collect decisions, show progress. It never writes files. |
| Shell (`desktop/`) | Electron | Own the window, the startup and failure screens, and the built-in browser used to collect pages behind a login. |

In development these are three processes. In a packaged app the interface is built ahead of time,
so only the shell and the core run.

## One origin in a packaged app

```text
Electron shell
  └── spawns ──> core on 127.0.0.1:<port>
                    ├── /            built interface (web/dist)
                    ├── /assets/*    fingerprinted assets
                    └── /api/*       JSON and server-sent events
```

The core is the only server. In development Vite runs on port 5174 and proxies `/api` to the core
on 8765, which keeps hot reload while leaving the interface same-origin. A packaged app has no
Vite: the core serves the built files and the API from one address, so there is no cross-origin
request to allow and no second port to find.

The shell never guesses that address. A packaged core binds a port, records it, and the shell reads
the record and waits for `/api/v1/health` before showing the interface.

## The core

| Package | Responsibility |
|---------|----------------|
| `acquire/` | Search providers, page fetch, extraction, file adapters (PDF, Office, images) |
| `writing/` | The project-level writing state and its six tools: web search, select a page, frame, compose, publish, read state |
| `agent/` | The agent kernel: loop, projection, typed tools, skills, run markers, cancellation, and the session store seam |
| `skills/` | The skill registry and the `skill` / `skill_read_file` tools |
| `rag/` | The optional hybrid retrieval backend behind `search` (BM25 + vectors + RRF) |
| `effects/` | The effect journal, page history and restore |
| `wiki/` | Page model: frontmatter, links, source/note sections, `index.md` / `log.md` maintenance; the agent's `search` and `wiki_read` tools |
| `notes/` | Notes written by hand, and where each one sits on a page |
| `ingest/` | Legacy compile pipeline and the shared page, citation and compile-cache helpers other paths still use |
| `pipeline/` | Legacy pipeline runs, read-only, plus the low-level material and framing helpers |
| `sessions/` | JSONL session files, the derived index, traces and the old-chat migration |
| `llm/` | The model client, its configuration, and the contract a prompt has to keep |
| `prompts/` | The instruction text for each model call, kept apart from the code that sends it |
| `api/` | The HTTP surface: routes, server-sent events, the local-only guard |
| `project/` | Project layout, activation pointer, lifecycle |
| `io/` | Reading a source file, and the title derived from it |
| `util/` | Cancellation, refusal codes, and the run log |

A run is one session loop that ends on the model's final message. The session transcript is the
record; the run's in-flight state lives in memory. A front-end button can start a one-shot node
run (the auto-frame node) with a scoped toolset; it is a normal run that ends the same way.
Streams use server-sent events; closing the stream pauses or cancels at the next boundary, and a
killed process is recovered from its run marker on the next start.

## A project on disk

A project is a directory. Everything the program needs is inside it.

| Path | Contents |
|------|----------|
| `config/brief.md` | What this wiki is for, in the author's words. Optional: a built-in default is used when it is absent |
| `raw/<kind>/` | Originals — **immutable**; written once when a source is kept. The kind folder (`web`, `pdf`, `office`, `media`) appears with its first file |
| `wiki/` | Generated pages, plus `index.md` and `log.md` |
| `notes/` | Notes written by hand, independent of the generated pages |
| `.noteagent/` | The program's own state: the writing state (`writing/state.json`), effects and page history, selections, session transcripts, caches |

`raw/` is immutable: the model reads originals and never writes them. A page is not written
directly either. The agent composes it in the project's writing state, and `write_wiki` publishes
one draft version through the page-write seam, which records the effect and the page history. A
person can edit the same state on the writing panel; a run's lease and a base-version check refuse
a save or publish that would overwrite work it did not see.

## Settings

Two scopes, because they are not the same thing.

The model connection, web search, and whether images are kept from a source belong to the
install. They live in a versioned document in the per-user configuration folder, and the keys
live in the operating system credential store. The settings screen writes both. Saving updates
the running process as well, so the next run uses the new values. A name that was already in the
environment when the process started is not overridden: the screen shows it and will not save a
different one.

What a wiki is for is `config/brief.md`, one per project. Session compaction
uses the kernel's defaults; there is no project-level limits file.

## Invariants

- **Originals are immutable.** Compilation reads `raw/`; only the acquisition step writes
  there, and only after a quality check.
- **Only selected passages reach a prompt.** The selection is explicit and stored per project.
- **Pages cite their passages.** Frontmatter carries `sources[]` locators of the form
  `path#block_id`, so a claim can be traced back to the bytes it came from.
- **The program maintains its own index.** `wiki/index.md` and `wiki/log.md` are derived; a
  publish updates them best-effort, and `rebuild-index` / `doctor` repair them. They are not the
  place to hand-edit a page listing.

## The local API

The API is bound to loopback and guarded: a request whose `Host` or `Origin` is not this machine is
refused before it reaches a route. There is no token, because the only clients are the local
interface, the local shell and a browser on the same machine.

`/api` is **not a stable contract** in this version. It changes with the interface, in the same
commit. Errors carry a stable `code`; the interface decides how to phrase it, which is what keeps
messages translatable.

## The shell

The shell is deliberately thin:

- a single instance, with a second launch bringing the existing window forward;
- a startup screen while the core answers, and a failure screen with the end of the core log and a
  retry button when it does not;
- a menu that follows the system language and opens the notes, configuration and log folders;
- a guest browser in its own session partition, so site cookies never mix with anything else and
  never reach Python.

Running from source, the shell does not start the core; it connects to the one the developer
started. A packaged app starts its own core from the runtime shipped beside it.

A core that stops on its own is started again, up to three times with growing waits, and a run that
lasted a minute gives that budget back — a crash after hours of work is not the same event as a
crash every second. The count and the last exit are written beside the core log, because a new
process cannot know how many before it there were. When the third attempt has also stopped, the
failure screen says so and the log is one click away. What this shell started, it also ends: quitting
the app is not a crash, so the exit is not answered with a restart.

## Diagnostics

The settings screen has a diagnostics section, and it only reads. It reports the version, how long
the running core has been up, the address it took, whether the shell is restarting it and how often
it had to, where the notes and the log are, and a short list of things worth fixing (no project, no
model configured, no log where one was expected). It is the screen to open before writing a report,
which is why nothing on it changes the state it describes.

The one request that leaves the machine is the update check. The app checks shortly after it
starts and every six hours, and Help → Check for updates… runs the same check on demand. It asks a
JSON document named by `NOTEAGENT_UPDATE_URL`, which an installation may not have; with none
configured the screen says so instead of implying it looked. There is no telemetry.

## Packaging shape

A release puts three things together:

1. the built interface, copied into the Python package (`scripts/build-web.mjs`);
2. a CPython 3.12 embeddable distribution with pre-fetched wheels (`scripts/bundle-runtime.mjs`);
3. the shell, packed with `electron-builder`.

The runtime is configured through `python312._pth` to see `Lib\site-packages` and the bundled
`src`, so the packaged app imports the same source tree that development runs. The installed app
keeps its configuration under `%APPDATA%\NoteAgent\` and its notes under `%USERPROFILE%\NoteAgent\`.

## License and provenance

MIT — see [LICENSE](../../LICENSE). The design began in the author's earlier repository; what it
owes to that work and to the GPL-3.0 reference the two were compared against, and how each
idea that survives on both sides differs here, is recorded in [`provenance.md`](provenance.md).
