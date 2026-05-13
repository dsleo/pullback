"""Vercel entrypoint for The Pullback FastAPI application."""

from pullback.api.app import create_app

app = create_app()
