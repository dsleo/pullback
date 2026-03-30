"""Bounded LaTeX block extraction, preferring single-environment capture over raw windows."""

from __future__ import annotations

import shlex

from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import normalize_environment_token, window_bounds

log = get_logger("forager.blocks")


async def fetch_latex_block(
    sandbox: SandboxRunner,
    arxiv_id: str,
    line_number: int,
    *,
    context_lines: int = 20,
    environment_name: str | None = None,
) -> str:
    with trace_span(
        "forager_tools.fetch_latex_block",
        arxiv_id=arxiv_id,
        line_number=line_number,
        context_lines=context_lines,
    ):
        path = await sandbox.resolve_paper_path(arxiv_id)
        normalized_env = normalize_environment_token(environment_name or "")
        if normalized_env:
            max_lines = 220
            command = (
                "python - <<'PY'\n"
                "import pathlib\n"
                "import re\n"
                "\n"
                f"path = pathlib.Path({path!r})\n"
                f"start_line = {line_number}\n"
                f"max_lines = {max_lines}\n"
                f"env = {normalized_env!r}\n"
                "text = path.read_text(errors='ignore')\n"
                "lines = text.splitlines()\n"
                "begin_re = re.compile(r'\\\\+begin\\{' + re.escape(env) + r'\\}', flags=re.IGNORECASE)\n"
                "end_re = re.compile(r'\\\\+end\\{' + re.escape(env) + r'\\}', flags=re.IGNORECASE)\n"
                "started = False\n"
                "depth = 0\n"
                "count = 0\n"
                "for idx in range(max(1, start_line), len(lines) + 1):\n"
                "    line = lines[idx - 1]\n"
                "    if not started:\n"
                "        if begin_re.search(line):\n"
                "            started = True\n"
                "            depth = 1\n"
                "            print(line)\n"
                "            count = 1\n"
                "            if end_re.search(line):\n"
                "                break\n"
                "        continue\n"
                "    if begin_re.search(line):\n"
                "        depth += 1\n"
                "    print(line)\n"
                "    count += 1\n"
                "    if end_re.search(line):\n"
                "        depth -= 1\n"
                "        if depth <= 0:\n"
                "            break\n"
                "    if count >= max_lines:\n"
                "        break\n"
                "PY"
            )
            snippet = await sandbox.run_shell(command)
            if snippet.strip():
                log.info(
                    "environment.fetched arxiv_id={} env={} chars={}",
                    arxiv_id,
                    normalized_env,
                    len(snippet),
                )
                return snippet

        count_command = f"wc -l < {shlex.quote(path)}"
        total_raw = (await sandbox.run_shell(count_command)).strip() or "0"
        total_lines = max(1, int(total_raw))
        start, end = window_bounds(line_number=line_number, total_lines=total_lines, radius=context_lines)

        command = f"sed -n '{start},{end}p' {shlex.quote(path)}"
        snippet = await sandbox.run_shell(command)
        log.info("block.fetched arxiv_id={} start={} end={} chars={}", arxiv_id, start, end, len(snippet))
        return snippet
