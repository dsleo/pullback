"""Agent-facing extraction tool facade for headers and LaTeX blocks."""

from __future__ import annotations

from ..extraction import extract_environment_token
from ..extraction.numbering import get_theorem_labels
from ..extraction.blocks import fetch_latex_block, fetch_latex_blocks
from ..extraction.headers import get_paper_headers
from ..models import LemmaHeader
from ..sandbox import SandboxRunner


class ExtractionTools:
    """Minimal façade for LaTeX extraction operations."""

    def __init__(self, sandbox: SandboxRunner) -> None:
        self._sandbox = sandbox

    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        return await get_paper_headers(self._sandbox, arxiv_id)

    async def fetch_latex_block(
        self,
        arxiv_id: str,
        line_number: int,
        *,
        context_lines: int = 20,
        environment_name: str | None = None,
    ) -> str:
        return await fetch_latex_block(
            self._sandbox,
            arxiv_id,
            line_number,
            context_lines=context_lines,
            environment_name=environment_name,
        )

    async def fetch_header_block(
        self,
        arxiv_id: str,
        line_number: int,
        header_line: str,
        *,
        context_lines: int = 20,
    ) -> str:
        environment_name = extract_environment_token(header_line)
        return await self.fetch_latex_block(
            arxiv_id,
            line_number,
            context_lines=context_lines,
            environment_name=environment_name,
        )

    async def fetch_header_blocks(
        self,
        arxiv_id: str,
        headers: list[LemmaHeader],
        *,
        context_lines: int = 20,
    ) -> dict[int, str]:
        return await fetch_latex_blocks(
            self._sandbox,
            arxiv_id,
            headers,
            context_lines=context_lines,
        )

    async def get_theorem_labels(self, arxiv_id: str) -> list[str]:
        return await get_theorem_labels(self._sandbox, arxiv_id)

    async def delete_paper(self, arxiv_id: str) -> None:
        """Free E2B disk space after a paper has been fully processed."""
        fn = getattr(self._sandbox, "delete_paper", None)
        if callable(fn):
            await fn(arxiv_id)

    def close(self) -> None:
        self._sandbox.close()
