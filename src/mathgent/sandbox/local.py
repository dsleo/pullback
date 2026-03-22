"""Local filesystem sandbox runner for development and deterministic tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..observability import get_logger, trace_span
from .base import SandboxRunner

log = get_logger("sandbox.local")


class LocalSandboxRunner(SandboxRunner):
    def __init__(self, paper_map: dict[str, Path]) -> None:
        self.paper_map = {k: Path(v).resolve() for k, v in paper_map.items()}

    def resolve_path(self, arxiv_id: str) -> Path:
        try:
            return self.paper_map[arxiv_id]
        except KeyError as exc:
            raise FileNotFoundError(f"No local LaTeX source registered for {arxiv_id}") from exc

    async def run_shell(self, command: str) -> str:
        with trace_span("sandbox.local.run_shell", command=command):
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_raw, stderr_raw = await proc.communicate()
            stdout = stdout_raw.decode()
            stderr = stderr_raw.decode()
            if proc.returncode not in (0, 1):
                log.error("run.failed returncode={} command={}", proc.returncode, command)
                raise RuntimeError(stderr.strip() or f"Command failed: {command}")
            return stdout

    async def resolve_paper_path(self, arxiv_id: str) -> str:
        resolved = str(self.resolve_path(arxiv_id))
        log.info("resolve arxiv_id={} path={}", arxiv_id, resolved)
        return resolved

    def close(self) -> None:
        return None
