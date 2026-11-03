# Changelog

Every release has one section. The release workflow copies the section matching
the app version into the GitHub Release body, so a version without a section
cannot be published.

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
