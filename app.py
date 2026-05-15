"""Vercel/production entrypoint for the Pullback demo (FastAPI app).

Vercel's FastAPI integration auto-detects an `app` instance at common
entrypoints like `app.py`. We re-export the existing app defined in
`api/index.py` so the demo works the same locally and on Vercel.
"""

from api.index import app  # noqa: F401

