"""Agent-facing extraction tool facade for header listing and bounded LaTeX block fetch."""

from __future__ import annotations

from ..extraction import extract_environment_name
from ..extraction.blocks import fetch_latex_block
from ..extraction.headers import get_paper_headers
from ..models import LemmaHeader
from ..sandbox import SandboxRunner


class ExtractionTools:
    """Agent-facing façade for LaTeX extraction operations."""

    def __init__(self, sandbox_runner: SandboxRunner) -> None:
        self._sandbox = sandbox_runner

    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        return await get_paper_headers(self._sandbox, arxiv_id)

    async def fetch_latex_block(
        self,
        arxiv_id: str,
        line_number: int,
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
        environment_name = extract_environment_name(header_line)
        return await self.fetch_latex_block(
            arxiv_id,
            line_number,
            context_lines=context_lines,
            environment_name=environment_name,
        )

    def close(self) -> None:
        self._sandbox.close()
