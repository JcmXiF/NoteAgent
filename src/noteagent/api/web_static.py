"""Where the built interface lives, and whether it is there.

The interface is built by Vite into ``web/dist`` and, for a release, copied
into ``noteagent/web`` so the package carries its own interface. A source
checkout has both; an installed package has only the second.
"""

from __future__ import annotations

import sys
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

INDEX_FILE = "index.html"

_REPO_WEB = ("web", "dist")
_PACKAGE_WEB = "web"


def web_dist_candidates(
    *,
    package_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """The two places a build may live, most useful first.

    The checkout comes first. Both places exist during development, and the copy
    inside the package is only refreshed by the release build, so it goes stale
    as soon as the frontend is rebuilt; preferring it would serve the previous
    interface without saying so.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    if package_dir is None:
        package_dir = Path(__file__).resolve().parents[1]
    return (repo_root.joinpath(*_REPO_WEB), package_dir / _PACKAGE_WEB)


def resolve_web_dist(
    *,
    package_dir: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """The directory holding the built interface, or ``None`` when none is there."""
    candidates = web_dist_candidates(package_dir=package_dir, repo_root=repo_root)
    for candidate in candidates:
        if (candidate / INDEX_FILE).is_file():
            return candidate
    print(
        "[web] no interface build found; looked in "
        + ", ".join(str(path) for path in candidates)
        + ". Build it with `npm run build` in web/",
        file=sys.stderr,
    )
    return None


class BuiltInterface(StaticFiles):
    """The built interface, with the caching its file names allow.

    Every asset carries a content hash in its name, so an asset may be cached for
    as long as its name is unchanged. The entry page must not be: it is what names
    those assets, and a cached copy from a previous install points at files that
    are no longer there. That is not a theoretical risk — an installed package has
    all its files dated at install time, and a browser judging freshness by date
    would consider the entry page fresh for years, so an upgraded window could
    come up blank.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # `get_path` normalizes with the platform's separator, so the rules below
        # compare path components, not the raw string.
        normalized = path.replace("\\", "/").strip("/")
        if normalized in ("", ".", INDEX_FILE) or normalized.endswith("/" + INDEX_FILE):
            response.headers["cache-control"] = "no-cache"
        elif normalized.startswith("assets/"):
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


__all__ = ["BuiltInterface", "INDEX_FILE", "resolve_web_dist", "web_dist_candidates"]
