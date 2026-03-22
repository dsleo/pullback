"""Header extraction by grep for theorem-like LaTeX environments with line numbers."""

from __future__ import annotations

import shlex

from ..models import LemmaHeader
from ..observability import get_logger, trace_span
from ..sandbox import SandboxRunner
from .parsing import parse_grep_headers

log = get_logger("forager.headers")


async def get_paper_headers(sandbox: SandboxRunner, arxiv_id: str) -> list[LemmaHeader]:
    with trace_span("forager_tools.get_paper_headers", arxiv_id=arxiv_id):
        path = await sandbox.resolve_paper_path(arxiv_id)
        command = "grep -En '\\\\+begin\\{(lemma|proposition|theorem)\\}' " f"{shlex.quote(path)} || true"
        out = await sandbox.run_shell(command)
        headers = parse_grep_headers(out)
        log.info("headers.found arxiv_id={} count={}", arxiv_id, len(headers))
        return headers
