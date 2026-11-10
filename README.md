# NOTEAGENT

An agent that turns curated sources into a persistent Markdown wiki.

NOTEAGENT reads material you have chosen — web pages, PDFs, Office documents, images, Markdown —
and compiles it into linked wiki pages. Compilation always passes through an explicit human gate:
the program proposes pages, you decide what gets written.

> **Status:** early development. The Python core and the desktop app run end to end on Windows.
> Interfaces and on-disk formats may still change.

## How it works

```text
[collect sources]   search cards ∥ built-in guest browser ∥ local files / paste
        ↓  you pick what to keep
[workbench]         saved sources, preview the original, select the passages you want
        ↓  selected passages
[compile]           plan topics → confirm pages → write
[wiki]              Markdown pages with sources[] pointing back to the exact passages
```

Design rules the implementation holds to:

- `raw/` is **immutable** — the model reads it, never writes it.
- The **human decides what counts as a source**; only selected passages reach a prompt.
- Pages carry `sources[]` locators (`path#block_id`) so every claim points back to a passage.
- `wiki/index.md` and `wiki/log.md` are maintained deterministically by the program.

## Requirements

- Python 3.12 (a conda environment is the supported path)
- Node.js 20.19+ or 22.12+ for the frontend and the Electron shell (Vite's requirement)
- An OpenAI-compatible LLM endpoint (for analysis, planning and writing)
- Optional: a search API key (Brave or Serper) for source discovery

## Getting started

```bash
# 1. Python environment
conda env create -f environment.yml
conda activate noteagent
pip install -e ".[api]"

# 2. Configuration — open the app's settings (the gear in the icon rail) and
#    fill in the model connection. The settings document lives in
#    %APPDATA%\NoteAgent\config.json; keys go to the operating system's
#    credential store. A variable already set in the environment wins.
#    For a headless checkout, the same NOTEAGENT_LLM_* and NOTEAGENT_SEARCH_*
#    variables can be set in the shell instead.

# 3. Create a wiki project and drop your material into its raw/
noteagent project init projects/my-wiki --set-active

# 4. Run the local API
noteagent serve                      # http://127.0.0.1:8765

# 5. Run the frontend (in another terminal)
cd web && npm install && npm run dev    # http://127.0.0.1:5174
```

The gear at the bottom of the icon rail edits those settings: the model and web search for this
computer, and a short description of what the current project is for. Saving takes effect without
a restart. A variable already set in the environment is shown but left alone.

### Configuration reference

The settings screen writes a versioned document at `%APPDATA%\NoteAgent\config.json`; keys go to
the operating system credential store under the service name `noteagent`. A value set in the
process environment wins over the document, which is how a script or CI chooses an endpoint
without touching the saved settings.

| Variable | Meaning |
|---|---|
| `NOTEAGENT_LLM_BASE_URL`, `NOTEAGENT_LLM_API_KEY`, `NOTEAGENT_LLM_MODEL` | The model connection |
| `NOTEAGENT_LLM_TEMPERATURE`, `NOTEAGENT_LLM_MAX_TOKENS`, `NOTEAGENT_LLM_TIMEOUT` | Generation parameters |
| `NOTEAGENT_SEARCH_PROVIDER`, `NOTEAGENT_SEARCH_API_KEY` | `brave`, `serper`, or `none` |
| `NOTEAGENT_SEARCH_MODE` | `auto`, `native`, or `external` |
| `NOTEAGENT_SEARCH_TIMEOUT`, `NOTEAGENT_SEARCH_COUNTRY`, `NOTEAGENT_SEARCH_LANG`, `NOTEAGENT_SEARCH_UI_LANG` | Search locale and limits |
| `NOTEAGENT_EXTRACT_MEDIA` | Extract images from PDF and HTML sources |
| `NOTEAGENT_HOME` | Where projects and notes live |
| `NOTEAGENT_PROJECT` | The project a command reads, instead of the active-project pointer |
| `NOTEAGENT_CONFIG_DIR` | Moves the configuration document (portable installs and tests) |

The Electron shell adds the built-in guest browser, which is what makes
"log in, read a page, keep it" work end to end:

```bash
cd desktop && npm install && npm run dev
```

`noteagent serve` has no hot reload — use `noteagent serve restart` after changing Python code.
Three terminals is the supported setup, and the order is not cosmetic: the shell reads the
interface's address once, so the Vite server has to be answering before `npm run dev` in
`desktop/` starts, or the shell stays on its failure screen.

### Upgrading a project made by an earlier version

Projects written before the flat layout keep their old folders until they are moved, and
`noteagent project doctor` reports one. The command is idempotent; a second run finds nothing
to do.

```bash
noteagent project migrate projects/my-wiki --dry-run   # print the plan
noteagent project migrate projects/my-wiki --yes       # carry it out
```

If two pages or two captures would end up with the same file name, the plan lists them and
`--yes` refuses to run: merging or renaming them is a decision for the person who knows what
the material is. What moves, and what a migrated project keeps, is in
[`docs/project-layout.md`](docs/project-layout.md).

## Desktop app

One command builds the frontend, assembles a private Python runtime, and packs both into an
installer. Nothing on the target machine needs Python or Node.

```powershell
node scripts/package.mjs          # installer
node scripts/package.mjs --dir    # unpacked app, faster to iterate on
```

The installer lands in `build/installer/`. The packaged app keeps its files apart:

| What | Where |
|---|---|
| Settings | `%APPDATA%\NoteAgent\config.json` — written by the settings screen |
| Keys | The operating system credential store, under the service name `noteagent` |
| Logs | `%APPDATA%\NoteAgent\logs\core.log` and `shell.log` |
| Notes and projects | `%USERPROFILE%\NoteAgent\<project>\` — visible and directly openable |

The menu opens each of those folders; the same menu follows the system language.
`NOTEAGENT_HOME` moves the notes elsewhere. Running from source does not use these paths.

## Repository layout

| Path | What it is |
|------|-----------|
| `src/noteagent/` | The Python core: ingest, wiki compilation, chat, acquisition, HTTP API |
| `web/` | The frontend (Vite + React + TypeScript) |
| `desktop/` | The Electron shell: application window, startup screens, guest browser |
| `scripts/` | Release build: the interface copied into the Python package, the bundled Python runtime, and the order the two must be done in |
| `tests/` | Python tests |
| `evals/` | Deterministic behaviour evals: the HTTP API, a scripted model, a temporary project |
| `projects/` | Wiki projects — **not in git**; each holds `config/`, `raw/`, `wiki/` |

The Python core is the only place business logic lives. The frontend calls the HTTP API; it never
shells out to the CLI, and it never decides what gets written.

## Documentation

| Document | Contents |
|----------|----------|
| [`docs/architecture.md`](docs/architecture.md) | How the core, the frontend and the shell fit together |
| [`docs/project-layout.md`](docs/project-layout.md) | What sits in a project directory, what a page is, and how projects migrate |
| [`docs/claim-layer.md`](docs/claim-layer.md) | The claim/evidence layer: what a claim is, where it lives, and how it decides when a page may be rewritten |
| [`docs/release-fixes.md`](docs/release-fixes.md) | The three repairs that ship before launch, and why they add nothing to the on-disk schema |
| [`docs/provenance.md`](docs/provenance.md) | What this design owes to earlier work and to its GPL-3.0 reference, and how each shared idea differs here |
| [`docs/development.md`](docs/development.md) | Running each part, the check suite, packaging, data locations |

## Contributing

Bug fixes and small corrections are welcome as a pull request; a new feature is worth an issue
first, since this project has a deliberately narrow idea of what it is. The checks to run before
opening one — `unittest`, the behaviour evals, the Markdown checker,
`typecheck`/`test`/`lint`/`build` — are listed in
[`docs/development.md`](docs/development.md); CI runs the same commands. The evals run with
`python -m evals` from the repository root; every case uses a temporary project and a scripted
model, so no key or network is needed.

- One concern per change, and do not reformat code you are not changing.
- Logic that can be reasoned about without a browser gets a test next to it.
- Text the reader sees goes through the i18n catalogs in `web/src/i18n/locales/`, never inline in
  a component. The wording rules `npm run check:terms` enforces — no paths or file names in
  user-facing copy, no internal jargon, no notes addressed to the developer instead of the
  reader, nothing that cannot be translated — are listed at the top of
  `web/scripts/check-terms.mjs`.
- Error messages follow the same rule: the core returns a stable `code`, and the interface decides
  how to say it.
- Comments explain why, not what, and point at code — never at a planning document or a
  conversation.

By contributing you agree that your changes are licensed under the MIT terms below.

## License

MIT — see [LICENSE](LICENSE).
