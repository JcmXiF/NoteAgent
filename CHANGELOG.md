# Changelog

Every release has one section. The release workflow copies the section matching
the app version into the GitHub Release body, so a version without a section
cannot be published.

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
