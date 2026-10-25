"""What a wiki page has to be, said once and injected wherever pages are written."""

from __future__ import annotations

#: Shared by every prompt that produces page text. It describes the artifact the
#: reader gets, not the pipeline that produces it: a prompt that explains the
#: pipeline invites the model to write about it.
PAGE_GUIDE = """
## What a page is

One page, one subject. A reader arrives from a link or a search with the name in
mind, and the page has to say what the thing is, how it works, and what it rests
on.

- Open with what the subject is, in the words the material uses.
- Make one argument. Each section follows from the one before it.
- Keep the material's own examples, notation and caveats: a derivation stays a
  derivation, a worked example stays worked through.
- State only what the material supports. Where two passages disagree, or hold
  only under different conditions, keep both and name the condition each holds
  under.
- Link an idea a reader would follow ([[slug]]); do not link every name that
  appears.
- A subject the material only names gets one honest sentence, or nothing. Do not
  pad out a lecture it does not support.

The page has one voice and never mentions its inputs: no "the source says", no
file names, no remark about drafts having been combined. The frontmatter's
`sources` list carries the provenance.
""".strip()


__all__ = ["PAGE_GUIDE"]
