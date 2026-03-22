"""E2B-backed sandbox runner with async-safe code execution wrappers."""

from __future__ import annotations

import asyncio

from e2b_code_interpreter import Sandbox

from ..observability import get_logger, trace_span
from .base import SandboxRunner
from .source_fetch import build_source_resolution_code

log = get_logger("sandbox.e2b")


class E2BSandboxRunner(SandboxRunner):
    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self._sandbox = sandbox
        self._paper_cache: dict[str, str] = {}
        self._run_lock = asyncio.Lock()

    @classmethod
    def create(cls) -> "E2BSandboxRunner":
        return cls(sandbox=Sandbox.create())

    async def _run_code_async(self, code: str):
        async with self._run_lock:
            if self._sandbox is None:
                self._sandbox = Sandbox.create()
            return await asyncio.to_thread(self._sandbox.run_code, code)

    async def run_shell(self, command: str) -> str:
        with trace_span("sandbox.e2b.run_shell", command=command):
            escaped_command = repr(command)
            code = (
                "import subprocess\n"
                f"cmd = {escaped_command}\n"
                "proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)\n"
                "print(proc.stdout, end='')\n"
                "if proc.returncode not in (0, 1):\n"
                "    raise RuntimeError(proc.stderr.strip() or f'Command failed: {cmd}')\n"
            )
            execution = await self._run_code_async(code)
            if execution.error is not None:
                raise RuntimeError(str(execution.error))
            stdout_lines = (execution.logs.stdout or []) if execution.logs is not None else []
            return "\n".join(stdout_lines)

    async def resolve_paper_path(self, arxiv_id: str) -> str:
        with trace_span("sandbox.e2b.resolve_paper_path", arxiv_id=arxiv_id):
            cached = self._paper_cache.get(arxiv_id)
            if cached:
                log.info("resolve.cache_hit arxiv_id={} path={}", arxiv_id, cached)
                return cached

            log.info("resolve.start arxiv_id={}", arxiv_id)
            code = build_source_resolution_code(arxiv_id)
            execution = await self._run_code_async(code)
            if execution.error is not None:
                log.error("resolve.failed arxiv_id={}", arxiv_id)
                raise RuntimeError(str(execution.error))

            stdout_lines = (execution.logs.stdout or []) if execution.logs is not None else []
            stdout = "\n".join(stdout_lines).strip()
            if not stdout:
                raise RuntimeError(f"Failed to resolve source path for {arxiv_id}")

            resolved = stdout.splitlines()[-1].strip()
            self._paper_cache[arxiv_id] = resolved
            log.info("resolve.done arxiv_id={} path={}", arxiv_id, resolved)
            return resolved

    def close(self) -> None:
        if self._sandbox is not None:
            self._sandbox.kill()
            self._sandbox = None
