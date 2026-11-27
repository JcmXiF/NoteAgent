# Provenance

What this project owes to earlier work, what has been rewritten since, and how each remaining
shared idea differs here. The licence is MIT.

Two things sit behind this project, and they are not the same:

- `noteagent_1`, the author's earlier repository (kept outside this one; first commit
  2026-07-12). This repository continues it. Its design notes, its decision log and its sample
  projects are where the product model was worked out.
- `llm_wiki` (<https://github.com/nashsu/llm_wiki>), GPL-3.0, Copyright (C) 2024–2026 Yong Su.
  It is a separate project by a different author, kept as a reference. Its product model, and
  in places its text, were the starting point; nothing from it is vendored here.

Neither fact is a licence to copy. A design can be inherited without a line being shared, and
an unrecorded inheritance is the kind of thing that reads as an accident later.

## The record

| When | What |
|------|------|
| 2024–2026 | `llm_wiki` is developed by Yong Su, outside this project, under GPL-3.0. |
| 2026-07-12 | `noteagent_1` starts: a planning document, then the first Python skeleton. |
| 2026-07-13 | `noteagent_1`'s project skeleton, built on the product model taken from `llm_wiki`: `project init`, a `raw/` and `wiki/` pair, `purpose.md` and `schema.md`. |
| 2026-07-21 | `noteagent_1` moves its prompt text out of the pipeline into its own modules (`83bbf20`). The analysis, generation and merge prompt text at that point was `llm_wiki`'s. |
| 2026-08-04 | The deterministic index becomes a step of `noteagent_1`'s pipeline (`eecaac7`). |
| 2026-08-11 | `noteagent_1` adds page-body merge (`0f99333`), translated from `llm_wiki`'s page merge. |
| 2026-09-15 | Deep Search rounds gain their own stored session. |
| 2026-09-16 | The interface alignment is accepted (D-043 in `noteagent_1`'s decision log): streaming, job state, window layout and controls follow `llm_wiki` on purpose, not its automatic write loop. |
| 2026-10-17 | This repository is created from `noteagent_1`; that repository's design document stayed behind. |
| 2026-10-19 | The layout plan (`docs/project-layout.md`) sets out what to retire and rewrite. |
| 2026-10-20 | Every prompt, page merging and the FILE-block parser are rewritten from behaviour; the compile cache is renamed. |

Two entries in that table are load-bearing for how the code should be read. The prompt modules
and the page-merge module were function-level ports: they worked, and their shape was
`llm_wiki`'s. Each has since been rewritten by writing down the behaviour first and then
implementing the rules, so the code is justified from its own docstrings rather than by
resemblance — the merge prompt included, which states the checks that will run on its answer
instead of repeating the reference's instruction list.

## Where a shared concept differs

One sentence per concept that still exists in both this project and `llm_wiki`. A resemblance
without a sentence here is unresolved, not endorsed.

| Concept | How this project differs |
|---------|--------------------------|
| A project folder the app owns | Here it has at most five top-level entries with one writer each, `notes/` and `config/` exist only once they hold something, and the two retired files became one `config/brief.md` with a built-in default. |
| The human gate | Here the decision is a stored per-page action — `write`, `skip` or `mention` — in the project's clip store, so the plan can be read back and re-run instead of living only in the session that made it. |
| Citations and source identity | Here a locator is the project-relative path plus a block or page suffix (`raw/web/x.html#b4`), and that same string keys the clip store and the media mirror, so a layout change rewrites one string instead of three. |
| Deterministic index | Here there are two sections chosen by folder — `Sources` and `Notes` — and a rebuild renders the whole file from the pages on disk, so a deleted page leaves no ghost bullet and there is no second listing to disagree with it. |
| Append-only log | Here the log records what a compile did, in the order it happened, and nothing reads it back to decide anything; the index is the only derived listing. |
| The wiki graph | Here it is navigation only: edges come from body links, repeated pairs are counted, and nothing is persisted, scored or clustered. |
| Chat over the wiki | Here an answer can plan pages or compile them, but the compile tool writes only pages a person already checked and ignores a confirmation list the model invents; no tool edits `wiki/` or `raw/` directly. |
| Notes beside the wiki | Here a note is a file under `notes/` with the page it belongs to, an anchor and the quoted passage, and the compile pipeline never reads `notes/` when it writes a page. |
| Deep Search | Here a round is a stored session with two modes over the same gate — plan from what the wiki lacks, or explore a whitelist — and it writes no page until a person confirms. |
| Activity panel | Here the panel is filled by each job's own event stream and keeps its state when the view changes, while the run log on disk is a separate record for diagnosis that the interface never reads. |
| Compile cache | Here a row names the outputs it wrote as well as the bytes it came from, and a lookup hits only when the bytes and the outputs both still hold. |
| Page merging | The rules are written down as R1–R7 at the top of the module, a refused merge gets one second attempt told what it failed (R7b) and otherwise keeps the page's existing `sources` rather than claiming passages its prose never absorbed. |
| FILE-block parsing | The parser defines an opener, a closer, a code fence and a writable path, and a block another opener cuts off is dropped instead of swallowing the pages after it. |
| Prompt text | Each prompt is organised around what its reader has to decide, and none of them names a page type or a folder: the merge prompt describes what its answer will be checked for, rather than listing instructions to be obeyed in order. |
| Settings a person can change | Here the model and web search belong to the install and are the `.env` the process already reads, and the brief belongs to one project. The screen writes those files. A name already set in the process environment stays put. The reference's settings catalog is not carried over: embeddings, skills, watches and the rest are absent because this product does not have them. |

The interface's visual language was aligned with `llm_wiki` deliberately (D-043, 2026-09-16),
so some screens will resemble it. That is a design decision recorded here rather than an
inheritance to be discovered.

## What is not claimed

- No `llm_wiki` source file, GPL header or template is vendored in this repository, and the
  licence is MIT throughout.
- An idea can be shared without the code being shared: a citation format, an index, a gate.
  Where that is so, the sentence above is the claim this project makes about it.
- The sentences describe this repository as it is. They do not assert what `llm_wiki`'s
  internals do, because that record lives outside this repository; where the difference is not
  visible from the code here, the row says what this project chose, not what the other one
  lacked.
- `noteagent_1` is the author's own work. `llm_wiki` is not: it belongs to a different author
  and carries a different licence. Nothing in this repository claims otherwise.

If a resemblance turns up with no row in the table, the honest move is to add the row — with
the difference, or with the intention to rewrite — rather than to leave it implicit.
