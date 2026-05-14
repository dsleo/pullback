"""Vercel entrypoint for The Pullback FastAPI application.

Uses the full demo app (with /stream SSE endpoint) to match local behavior.
"""

import sys
from pathlib import Path

# Add paths so modules can be imported on Vercel
repo_root = Path(__file__).parent
src_path = repo_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from demo.app import app
