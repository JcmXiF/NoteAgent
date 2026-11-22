---
name: writing
description: Use when the reader asks to write, update, plan, compile or reorganize wiki pages — 写一页、整理成一页、对比页、学习路径、发起规划、编译、改写、补一节.
---

# Writing a wiki page

The state is the one truth. A tool's answer tells you what happened; read the
content it touched with `get_state`.

## Recipe

1. `get_state` if you have not read this project's state in this run.
2. `web_search(query)`; then `get_state({searchId: ...})` to read the hits.
3. `select_page({hitId})`; then `get_state({pageId})` to read the blocks.
4. `select_frame({pageId, blockIds})` for each meaningful group of blocks.
   One to four frames per page is usually enough. Do not mix distant material
   into one frame.
5. `compose_wiki({wikiFile, baseDraftVersion, sections})`. Sections are the
   page's ordered pieces:
   - short pages: 2–4 sections in one call; long pages: one section at a time;
   - before the next call, read `get_state({section: "draft"})` and use the
     current page and section versions;
   - each section has a stable id and a heading; the body is Markdown;
   - every claim that came from material cites the frame id it came from —
     never invent a citation, and never cite a frame you did not draw;
   - a section a person edited is refused unless you read it again and pass
     `overwrite: true`;
   - the draft is a plan you can revise, not a promise: compose, read, revise.
6. `write_wiki({wikiFile, draftVersion})`. The tool renders the frontmatter
   `sources` and the inline markers from your citations. Never write a source
   marker or a `sources:` line by hand.

## What a wiki page looks like

- The tool owns the frontmatter: it keeps what the page already has and writes
  `updated` and `sources`.
- The body starts with `# title`, then `##` sections in the order you composed
  them.
- Prose, definitions, formulas and relationships — not a list of links and not
  a transcript of your search.

## Pitfalls

- **stale frame**: the frame changed after you cited it. The page still
  publishes, with a warning; if the claim moved, re-read the frame and rewrite
  that section.
- **missing target**: a cited frame, page or block is gone. `write_wiki` refuses;
  pick the material again.
- **a person's edit**: a section may be `humanEdited`. Ask before replacing it;
  if you replace it, re-read it first and pass `overwrite: true`.
- **the save lock**: while a run holds the writing state, the person cannot
  save. Finish the run and release the state.
- Tool errors are observations, not crashes: read the message, correct the
  call, and continue.
