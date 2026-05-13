"""Sandbox runner protocol for command execution and LaTeX source path resolution."""

from __future__ import annotations

from typing import Protocol


class SandboxRunner(Protocol):
    async def run_shell(self, command: str) -> str: ...
    async def resolve_paper_path(self, arxiv_id: str) -> str: ...
    def close(self) -> None: ...
