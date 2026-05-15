"""Embedded/static asset fallbacks for the Pullback demo.

Preferred source of truth for the UI is `demo/static/*`. These constants are
used as a last-resort fallback if file-backed assets are unavailable at runtime.
"""

from __future__ import annotations

from pathlib import Path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO_STATIC_DIR = REPO_ROOT / "demo" / "static"


INDEX_HTML = (
    _read_text(_DEMO_STATIC_DIR / "index.html")
    or """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Pullback</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<h1>The Pullback</h1>
<p>Static assets are missing. Please ensure <code>demo/static</code> is deployed.</p>
<script src="/static/app.js"></script>
</body>
</html>
"""
)

STYLE_CSS = _read_text(_DEMO_STATIC_DIR / "style.css") or "/* missing style.css */"

APP_JS = _read_text(_DEMO_STATIC_DIR / "app.js") or "/* missing app.js */"

