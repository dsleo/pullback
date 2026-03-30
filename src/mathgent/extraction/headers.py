"""Header extraction for theorem-like LaTeX environments with line numbers."""

from __future__ import annotations

from ..models import LemmaHeader
from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import THEOREM_LIKE_KEYWORDS, parse_grep_headers

log = get_logger("forager.headers")


def _build_header_scan_command(path: str) -> str:
    return (
        "python - <<'PY'\n"
        "import pathlib\n"
        "import re\n"
        "\n"
        f"path = pathlib.Path({path!r})\n"
        "text = path.read_text(errors='ignore')\n"
        "lines = text.splitlines()\n"
        f"keywords = {tuple(THEOREM_LIKE_KEYWORDS)!r}\n"
        "\n"
        "def normalize_env(token: str) -> str | None:\n"
        "    env = token.strip().lower()\n"
        "    while env.endswith('*'):\n"
        "        env = env[:-1].strip()\n"
        "    if not env or not re.fullmatch(r'[a-z@][a-z0-9@:_-]*', env):\n"
        "        return None\n"
        "    return env\n"
        "\n"
        "def looks_theorem_like(env: str) -> bool:\n"
        "    return any(keyword in env for keyword in keywords)\n"
        "\n"
        "begin_re = re.compile(r'\\\\+begin\\{(?P<env>[^}]+)\\}', flags=re.IGNORECASE)\n"
        "for line_number, line in enumerate(lines, start=1):\n"
        "    for match in begin_re.finditer(line):\n"
        "        env = normalize_env(match.group('env'))\n"
        "        if env is None:\n"
        "            continue\n"
        "        if looks_theorem_like(env):\n"
        "            print(f'{line_number}:{line.strip()}')\n"
        "            break\n"
        "PY"
    )


async def get_paper_headers(sandbox: SandboxRunner, arxiv_id: str) -> list[LemmaHeader]:
    with trace_span("forager_tools.get_paper_headers", arxiv_id=arxiv_id):
        path = await sandbox.resolve_paper_path(arxiv_id)
        out = await sandbox.run_shell(_build_header_scan_command(path))
        headers = parse_grep_headers(out)
        log.info("headers.found arxiv_id={} count={}", arxiv_id, len(headers))
        return headers
