"""Vercel entrypoint for The Pullback FastAPI application."""

import sys
from pathlib import Path

# Add src to path so pullback module can be imported on Vercel
# (when not installed in editable mode via pip install -e .)
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from pullback.api.app import create_app

app = create_app()
