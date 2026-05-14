"""Vercel Serverless Function entrypoint (FastAPI + SSE).

Vercel treats Python files under `api/` as Serverless Functions. We re-export the
FastAPI `app` from the repo root so the same codepath can run locally too.
"""

from __future__ import annotations

from app import app  # noqa: F401

