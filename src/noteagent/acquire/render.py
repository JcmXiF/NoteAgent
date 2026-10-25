"""Render a derived block as a standalone reader document."""

from __future__ import annotations

import html as html_lib

_READER_CSS = """
body{font-family:system-ui,sans-serif;max-width:44rem;margin:1.5rem auto;padding:0 1rem;
line-height:1.65;color:#1b1b1b;background:#fff;}
img,svg{max-width:100%;height:auto;}
pre,code{font-family:ui-monospace,Consolas,monospace;font-size:.9em;}
pre{overflow:auto;padding:.75rem;background:#f4f4f4;}
table{border-collapse:collapse;width:100%;}
td,th{border:1px solid #ccc;padding:.35rem .5rem;}
"""


def wrap_article_document(
    *,
    title: str,
    source_url: str,
    capture_method: str,
    fidelity: str,
    body_html: str,
    extra_css: str = "",
) -> str:
    """A block document in the reader's shell.

    *extra_css* is for the parts a particular kind of original has and the shared
    reader stylesheet does not — a markdown source's headings, say. It is appended
    to the shared rules rather than replacing them, so a document always keeps the
    body it is read in.
    """
    styles = _READER_CSS.strip()
    if extra_css.strip():
        styles = f"{styles}\n{extra_css.strip()}"
    safe_title = html_lib.escape(title or source_url or "untitled")
    metas = (
        ("noteagent:source_url", source_url),
        ("noteagent:capture_method", capture_method),
        ("noteagent:fidelity", fidelity),
    )
    meta_tags = "\n".join(
        f'  <meta name="{html_lib.escape(name)}" content="{html_lib.escape(value)}">'
        for name, value in metas
        if value
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="referrer" content="no-referrer">\n'
        f"{meta_tags}\n"
        f"  <title>{safe_title}</title>\n"
        f"  <style>{styles}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<article>\n{body_html.strip()}\n</article>\n"
        "</body>\n"
        "</html>\n"
    )
