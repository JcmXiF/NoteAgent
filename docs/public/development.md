# Development

## Requirements

- Python 3.12. A conda environment is the supported path; any virtualenv works if the dependencies
  install there.
- Node.js 20.19+, or 22.12+ on the 22 line. That is Vite's own floor, and both `web/` and
  `desktop/` need it.
- Windows is the platform releases are built for. The core is plain Python and runs elsewhere;
  `desktop/` has Windows-specific paths in its packaging configuration.

## Setup

```bash
conda env create -f environment.yml
conda activate noteagent
pip install -e ".[api]"

cd web && npm install
cd ../desktop && npm install
```

Configuration is entered in the app: the gear in the icon rail writes the versioned document at
`%APPDATA%\NoteAgent\config.json` and puts keys in the operating system credential store. The CLI
applies the same document before a command reads the model or search settings, so a command run in
a terminal and a run started by the app use one source of truth. A variable already set in the
environment wins over both; that is how a script or CI chooses an endpoint without touching the
document.

### Configuration reference

The settings screen writes a versioned document at `%APPDATA%\NoteAgent\config.json`; keys go to the operating system credential store under the service name `noteagent`. A value set in the process environment wins over the document, which is how a script or CI chooses an endpoint without touching the saved settings.

| Variable | Meaning |
|---|---|
| `NOTEAGENT_LLM_BASE_URL`, `NOTEAGENT_LLM_API_KEY`, `NOTEAGENT_LLM_MODEL` | The model connection |
| `NOTEAGENT_LLM_TEMPERATURE`, `NOTEAGENT_LLM_MAX_TOKENS`, `NOTEAGENT_LLM_TIMEOUT` | Generation parameters |
| `NOTEAGENT_LLM_CONTEXT_WINDOW` | The model's context window for compaction (default 32000); an advanced environment override, not in the settings document |
| `NOTEAGENT_SEARCH_PROVIDER`, `NOTEAGENT_SEARCH_API_KEY` | `brave`, `serper`, or `none` |
| `NOTEAGENT_SEARCH_MODE` | `auto`, `native`, or `external` |
| `NOTEAGENT_SEARCH_TIMEOUT`, `NOTEAGENT_SEARCH_COUNTRY`, `NOTEAGENT_SEARCH_LANG`, `NOTEAGENT_SEARCH_UI_LANG` | Search locale and limits |
| `NOTEAGENT_EXTRACT_MEDIA` | Extract images from PDF and HTML sources |
| `NOTEAGENT_RAG_ENABLED` | Turn hybrid retrieval on (`false` by default) |
| `NOTEAGENT_RAG_EMBEDDING_ENDPOINT`, `NOTEAGENT_RAG_EMBEDDING_MODEL`, `NOTEAGENT_RAG_EMBEDDING_QUERY_PREFIX` | The local embedding service; the model defaults to Qwen3-Embedding-0.6B |
| `NOTEAGENT_RAG_EMBEDDING_BATCH`, `NOTEAGENT_RAG_EMBEDDING_CONCURRENCY`, `NOTEAGENT_RAG_EMBEDDING_RETRIES`, `NOTEAGENT_RAG_EMBEDDING_QUERY_TIMEOUT`, `NOTEAGENT_RAG_EMBEDDING_PAGE_TIMEOUT`, `NOTEAGENT_RAG_EMBEDDING_API_KEY` | Advanced embedding controls; the key may also come from the credential store |
| `NOTEAGENT_RAG_RERANK_ENABLED`, `NOTEAGENT_RAG_RERANK_ENDPOINT`, `NOTEAGENT_RAG_RERANK_MODEL`, `NOTEAGENT_RAG_RERANK_CANDIDATES`, `NOTEAGENT_RAG_RERANK_TOP_N`, `NOTEAGENT_RAG_RERANK_API_KEY`, `NOTEAGENT_RAG_RERANK_TIMEOUT` | The optional rerank service (off by default); its key may also come from the credential store |
| `NOTEAGENT_HOME` | Where projects and notes live |
| `NOTEAGENT_PROJECT` | The project a command reads, instead of the active-project pointer |
| `NOTEAGENT_CONFIG_DIR` | Moves the configuration document (portable installs and tests) |

The retrieval switch, the embedding service settings (address, model, query prefix, batch, concurrency and key) and the optional reranker are also on the settings screen, under Retrieval. The remaining RAG variables (retry and timeout controls) are read from the environment only, and a value set there still wins over the saved document.

## Running

### Three terminals

The supported paths are the desktop app and `python -m noteagent.api`; the `noteagent` command is
an internal developer wrapper. Three processes, started in this order:

```bash
python -m noteagent.api      # 1. the core, http://127.0.0.1:8765
cd web && npm run dev        # 2. the interface, http://127.0.0.1:5174
cd desktop && npm run dev    # 3. the shell
```

The order is not cosmetic. The shell reads the interface's address once and shows a failure screen
instead of retrying, so starting it before the Vite server answers leaves a shell that has to be
closed and reopened. The core keeps its own terminal because it is restarted on its own far more
often than the other two, and it has no hot reload.

### Starting one of them

The core can be run on its own, whether or not the other two are up:

```bash
python -m noteagent.api          # core, http://127.0.0.1:8765
```

Stop it with Ctrl-C and run it again after changing Python. The interface alone is enough to work
on the frontend: it reaches the API through Vite's `/api` proxy, so opening 5174 in a browser
needs no shell.

### The interface served by the core

To see exactly what a release serves — one origin, no Vite:

```bash
cd web && npm run build
cd .. && node scripts/build-web.mjs   # copies dist into src/noteagent/web/
python -m noteagent.api
```

Then open `http://127.0.0.1:8765/`.

### Search provider

Web search is optional: without a provider, only the material you hand in is ingested. The key
comes from [Brave's search dashboard](https://api-dashboard.search.brave.com/) (the Search plan,
then API Keys), or from Serper as the alternative. The settings screen shows which provider is
configured; the internal `noteagent acquire status` prints it without the key, and
`noteagent acquire search "KL散度"` runs one live query.

Brave's `search_lang` is not plain ISO 639-1: use `zh-hans` or `zh-hant`. `zh` is accepted in the
settings file and sent as `zh-hans`.

## Checks

From the repository root, one command runs the mechanical checks below; the eval commands are
local-only:

```bash
node scripts/check.mjs
```

It picks a Python that can import the core and the API extra, puts `src` on the path,
and fails with the variable to set rather than a later `No module named noteagent`.
Use `node scripts/check.mjs --python PATH` (or `NOTEAGENT_PYTHON`) to name one.

The individual commands, for a targeted run:

```bash
python -m unittest discover -s tests
python -m evals            # local deterministic evals (not in CI); no key, no network
python -m evals.quality.v4.run --live  # local atoms + real web smoke; CI refused
python -m evals.quality.v4.quality_run --live A,B,C,D,E  # judge calibration + live cases; real model; CI refused
python tests/check-docs.py

cd web
npm run typecheck
npm test
npm run lint
npm run build            # includes the translation and wording checks

cd ../desktop
npm test
```

CI runs the mechanical checks (tests, docs, typecheck, lint, build); the behaviour and quality
evals are local-only. `python tests/check-docs.py` checks the Markdown in this repository for the
faults that have actually happened: unbalanced code fences, table rows with the wrong number of
columns, BOM or CRLF endings, and section references that no longer name a heading.

Three frontend tests — `sections.corpus`, `wikilink.corpus`, `wiki-locators.corpus` — read every
page under `projects/` and are skipped where that folder is absent, which is every clone and CI.
They answer whether a rule holds on pages a model wrote, so run them locally before changing the
link, section or frontmatter rules; a green CI says nothing about them.

### Temporary cleanup on Windows

Tests and evals remove their temporary projects with `noteagent.util.tempdir.remove_tree`. It
retries a handle that is still closing (`PermissionError`, WinError 32, `EACCES`, `EBUSY`) and a
folder a writer refills after the scan (`ENOTEMPTY`, WinError 145) six times over 0.5 s before
raising; a missing tree is already removed, and any other error is raised from the first attempt.
Clients, app patchers and the eval case are closed before the tree is removed, and a test whose
executor is still stopping joins it first, so the retry only covers the instant a background
writer needs to exit. Do not add a blanket retry to `check.mjs`: a failure there should name the
step that failed.

## Agent worktrees

An agent ticket can work in its own checkout without repeating the setup:

```bash
node scripts/agent-worktree.mjs new <slug> [--base <ref>]   # .claude/worktrees/<slug>
node scripts/agent-worktree.mjs rm <slug>
```

`new` creates `.claude/worktrees/<slug>` on branch `ticket/<slug>` from `--base` (the main
checkout's current HEAD by default). It links the main checkout's `web/node_modules`,
`desktop/node_modules` and `.scratch`. It also makes the local docs visible: local doc folders
such as `docs/adr` are linked, and local `docs/*.md` files are copied, because a file symlink
needs Developer Mode or elevation on Windows. The tracked `docs/agents` directory stays the
worktree's own copy; a target that already exists is never replaced. A missing `node_modules` is
skipped with a warning, so `npm install` in that workspace is still the setup step.

`rm` unlinks those junctions and symlinks first - removing the link, never its target - and then
runs `git worktree remove --force`. The branch is kept, so deleting it is a separate, deliberate
step. Do not run `git worktree remove` first: git walks through a junction and can empty the main
checkout's `node_modules`.

## Packaging

```powershell
node scripts/package.mjs          # installer
node scripts/package.mjs --dir    # unpacked app only
```

The command runs four steps in a fixed order: build `web` → copy the build into the Python
package → assemble the runtime → run `electron-builder`. Output goes to `build/installer/`:
`NoteAgent-<version>-x64.exe` and `win-unpacked/`.

The runtime step downloads the embeddable CPython build and the Windows wheels it needs, then
installs the wheels into the bundled `Lib\site-packages`. Both downloads are cached under
`build/cache/`, so the first run takes a few minutes and later ones are quick.

To check what the build produced, without launching the Electron shell:

```powershell
node scripts/smoke-packaged.mjs          # after a build
node scripts/smoke-packaged.mjs --keep   # keep the temporary project and core log
```

It starts `build/runtime/core/python/python.exe -m noteagent.api`, waits for the address the core
records, and checks four things: an old `chats/*.json` file is migrated before any route runs,
`/api/v1/health` reports `desktop/package.json`'s version, `/` serves the built interface, and a
scripted model completes one chat flow (two scripted turns).
The model is a script, so no key or network is used. Nightly and release run the same command
after packaging. A failed run keeps the temporary project, its session events, `core.log` and
`chat.log` under `build/smoke-packaged`; CI uploads that folder. A successful run removes it
unless `--keep` is passed.

## Where data lives

| | Running from source | Installed app |
|---|---|---|
| Projects and notes | `projects/` in the repository | `%USERPROFILE%\NoteAgent\<project>\` |
| Active project pointer | `.noteagent-project` in the repository | `%USERPROFILE%\NoteAgent\.noteagent-project` |
| Settings | `%APPDATA%\NoteAgent\config.json` | `%APPDATA%\NoteAgent\config.json` |
| Keys | the operating system credential store | the operating system credential store |
| Logs | the core's terminal | `%APPDATA%\NoteAgent\logs\core.log` and `shell.log` |

Deleting a project, resetting a notes library and deleting a source each move what they remove into
`<projects>/.trash`, a peer of the project folders, and keep it for thirty days. Restoring puts it
back; anything already at the destination moves into the trash in turn, so a restore can itself be
undone. The Recently deleted dialog in the project bar reads that folder.

The gear in the app edits that file and the current project's brief. A variable already set in
the environment is shown and not overwritten.

`NOTEAGENT_HOME` moves the data home; when it is set, projects live directly under it. The
configuration home is always under `%APPDATA%`. `NOTEAGENT_PROJECT` names a project directory
directly and takes precedence over the pointer file.

## Ports and process records

The core binds its own port rather than being told one that was free a moment ago. In development
that is 8765; a packaged core asks for 8765 and falls back to a free port if it is taken.

Each running core writes `<temp>\noteagent-serve\<pid>.json` with the address it actually serves,
so a core that fell back to another port can still be found.

## Failures

In development, read the core's terminal — it has the full traceback. A packaged app cannot open a
terminal, so the shell writes the core's stdout and stderr to `core.log` byte for byte and shows
the last lines on its failure screen; a failure that happens before Python starts still leaves a
message there.

Logs rotate once at startup: a `core.log` larger than 5 MB becomes `core.log.1` and a fresh file is
started. The previous run is therefore always available when the current one cannot start.
