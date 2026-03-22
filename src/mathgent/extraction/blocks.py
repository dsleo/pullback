"""Bounded LaTeX block extraction, preferring single-environment capture over raw windows."""

from __future__ import annotations

import shlex

from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import SUPPORTED_ENVIRONMENTS, window_bounds

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
        normalized_env = (environment_name or "").strip().lower()
        if normalized_env in SUPPORTED_ENVIRONMENTS:
            env_quoted = shlex.quote(normalized_env)
            max_lines = 220
            command = (
                f"sed -n '{line_number},$p' {shlex.quote(path)} | "
                f"awk -v env={env_quoted} -v max_lines={max_lines} '"
                "BEGIN {"
                "started=0; depth=0; count=0; "
                "begin_pat=\"\\\\\\\\+begin\\\\{\" env \"\\\\}\"; "
                "end_pat=\"\\\\\\\\+end\\\\{\" env \"\\\\}\""
                "} "
                "{"
                "line=$0; "
                "if (!started) {"
                "if (line ~ begin_pat) {started=1; depth=1; print line; count=1; if (line ~ end_pat) exit} "
                "next"
                "} "
                "if (line ~ begin_pat) depth++; "
                "print line; count++; "
                "if (line ~ end_pat) {depth--; if (depth <= 0) exit} "
                "if (count >= max_lines) exit"
                "}'"
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
