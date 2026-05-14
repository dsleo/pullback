"""Legacy Vercel entrypoint.

This file is intentionally kept for compatibility with older Vercel configs, but
the primary deployment path is configured in `vercel.json` to build `app.py`
directly via `@vercel/python`.
"""

from __future__ import annotations

from app import app  # noqa: F401
