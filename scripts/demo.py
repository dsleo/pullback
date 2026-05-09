"""Compatibility shim — the demo has moved to demo/app.py.

Run:
    set -a && source .env.local && set +a
    python demo/app.py
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).parent.parent / "demo" / "app.py"),
        run_name="__main__",
    )
