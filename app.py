"""Vercel entrypoint for The Pullback.

Imports the full demo app (which includes /stream SSE endpoint and static files)
to provide the same experience as running locally.
"""

import sys
from pathlib import Path

# Ensure imports work in Vercel's serverless environment
repo_root = Path(__file__).parent
src_path = repo_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Import the full demo app (has /stream endpoint, static files, everything)
from demo.app import app  # noqa: F401

# Vercel requires the FastAPI app to be named 'app'
