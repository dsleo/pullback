"""Bounded LaTeX block extraction, preferring single-environment capture over raw windows."""

from __future__ import annotations

import json
import shlex

from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import extract_environment_token, normalize_environment_token, window_bounds

log = get_logger("extraction.blocks")


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
            try:
                snippet = await sandbox.run_shell(command)
            except RuntimeError as exc:
                log.warning(
                    "environment.fetch_failed arxiv_id={} env={} error={}",
                    arxiv_id,
                    normalized_env,
                    exc,
                )
                snippet = ""
            if snippet.strip():
                log.info(
                    "environment.fetched arxiv_id={} env={} chars={}",
                    arxiv_id,
                    normalized_env,
                    len(snippet),
                )
                return snippet

        count_command = f"wc -l < {shlex.quote(path)}"
        try:
            total_raw = (await sandbox.run_shell(count_command)).strip() or "0"
        except RuntimeError as exc:
            log.warning("block.count_failed arxiv_id={} path={} error={}", arxiv_id, path, exc)
            return ""
        total_lines = max(1, int(total_raw))
        start, end = window_bounds(line_number=line_number, total_lines=total_lines, radius=context_lines)

        command = f"sed -n '{start},{end}p' {shlex.quote(path)}"
        try:
            snippet = await sandbox.run_shell(command)
        except RuntimeError as exc:
            log.warning("block.fetch_failed arxiv_id={} path={} error={}", arxiv_id, path, exc)
            return ""
        log.info("block.fetched arxiv_id={} start={} end={} chars={}", arxiv_id, start, end, len(snippet))
        return snippet


async def fetch_latex_blocks(
    sandbox: SandboxRunner,
    arxiv_id: str,
    headers,
    *,
    context_lines: int = 20,
) -> dict[int, str]:
    with trace_span("forager_tools.fetch_latex_blocks", arxiv_id=arxiv_id, count=len(headers)):
        if not headers:
            return {}

        path = await sandbox.resolve_paper_path(arxiv_id)
        payload = []
        for header in headers:
            env = extract_environment_token(header.line or "")
            payload.append(
                {
                    "line_number": header.line_number,
                    "env": normalize_environment_token(env or ""),
                }
            )
        payload_json = json.dumps(payload, ensure_ascii=True)
        max_lines = 220
        command = (
            "python - <<'PY'\n"
            "import json\n"
            "import pathlib\n"
            "import re\n"
            "\n"
            f"path = pathlib.Path({path!r})\n"
            f"items = json.loads({payload_json!r})\n"
            f"radius = {context_lines}\n"
            f"max_lines = {max_lines}\n"
            "text = path.read_text(errors='ignore')\n"
            "lines = text.splitlines()\n"
            "total_lines = max(1, len(lines))\n"
            "\n"
            "def window_bounds(line_number, total_lines, radius):\n"
            "    start = max(1, line_number - radius)\n"
            "    end = min(total_lines, line_number + radius)\n"
            "    return start, end\n"
            "\n"
            "results = {}\n"
            "for item in items:\n"
            "    line_number = int(item.get('line_number', 0) or 0)\n"
            "    env = str(item.get('env') or '').strip()\n"
            "    if line_number <= 0:\n"
            "        continue\n"
            "    snippet = ''\n"
            "    if env:\n"
            "        begin_re = re.compile(r'\\\\+begin\\{' + re.escape(env) + r'\\}', flags=re.IGNORECASE)\n"
            "        end_re = re.compile(r'\\\\+end\\{' + re.escape(env) + r'\\}', flags=re.IGNORECASE)\n"
            "        started = False\n"
            "        depth = 0\n"
            "        count = 0\n"
            "        snippet_lines = []\n"
            "        for idx in range(max(1, line_number), total_lines + 1):\n"
            "            line = lines[idx - 1]\n"
            "            if not started:\n"
            "                if begin_re.search(line):\n"
            "                    started = True\n"
            "                    depth = 1\n"
            "                    snippet_lines.append(line)\n"
            "                    count = 1\n"
            "                    if end_re.search(line):\n"
            "                        break\n"
            "                continue\n"
            "            if begin_re.search(line):\n"
            "                depth += 1\n"
            "            snippet_lines.append(line)\n"
            "            count += 1\n"
            "            if end_re.search(line):\n"
            "                depth -= 1\n"
            "                if depth <= 0:\n"
            "                    break\n"
            "            if count >= max_lines:\n"
            "                break\n"
            "        if snippet_lines:\n"
            "            snippet = '\\n'.join(snippet_lines)\n"
            "    if not snippet:\n"
            "        start, end = window_bounds(line_number, total_lines, radius)\n"
            "        snippet = '\\n'.join(lines[start - 1:end])\n"
            "    results[str(line_number)] = snippet\n"
            "\n"
            "print(json.dumps(results))\n"
            "PY"
        )
        try:
            raw = await sandbox.run_shell(command)
        except RuntimeError as exc:
            log.warning("blocks.fetch_failed arxiv_id={} error={}", arxiv_id, exc)
            return {}
        raw = raw.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("blocks.decode_failed arxiv_id={}", arxiv_id)
            return {}
        out: dict[int, str] = {}
        for key, snippet in parsed.items():
            try:
                line_number = int(key)
            except (TypeError, ValueError):
                continue
            out[line_number] = str(snippet or "")
        log.info("blocks.fetched arxiv_id={} count={}", arxiv_id, len(out))
        return out
