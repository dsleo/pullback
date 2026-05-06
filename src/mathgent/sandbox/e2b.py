"""E2B-backed sandbox runner with async-safe code execution wrappers."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import httpx

# Patch httpx.Client to silently accept but ignore proxy=None parameter for e2b compatibility
_original_client_init = httpx.Client.__init__

def _patched_client_init(self, *args, **kwargs):
    # Remove proxy=None to avoid e2b compatibility issues
    if kwargs.get('proxy') is None:
        kwargs.pop('proxy', None)
    return _original_client_init(self, *args, **kwargs)

httpx.Client.__init__ = _patched_client_init

from e2b_code_interpreter import Sandbox

from ..config import get_config
from ..observability import get_logger, trace_span
from .base import SandboxRunner
from .source_fetch import build_source_resolution_code

log = get_logger("sandbox.e2b")


class E2BSandboxRunner(SandboxRunner):
    def __init__(
        self,
        sandbox: Sandbox | None = None,
        *,
        sandbox_factory: Callable[[], Sandbox] | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._paper_cache: dict[str, str] = {}
        # Semaphore allows up to 3 concurrent E2B calls; prevents sandbox overload while allowing parallelism
        self._concurrency_limit = asyncio.Semaphore(3)
        self._sandbox_factory = sandbox_factory or self._default_factory

    @classmethod
    def create(cls) -> "E2BSandboxRunner":
        return cls(sandbox=cls._default_factory())

    @staticmethod
    def _default_factory() -> Sandbox:
        cfg = get_config()
        timeout = cfg["sandbox"]["e2b_timeout_seconds"]
        if timeout and timeout > 0:
            return Sandbox.create(timeout=timeout)
        return Sandbox.create()

    def _reset_sandbox(self) -> None:
        if self._sandbox is not None:
            try:
                self._sandbox.kill()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        self._sandbox = self._sandbox_factory()

    @staticmethod
    def _should_recreate(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "sandbox was not found" in message or "sandbox not found" in message:
            return True
        if "timeoutexception" in name and "sandbox" in message:
            return True
        if isinstance(exc, httpx.RemoteProtocolError):
            return True
        if "incomplete chunked read" in message or "peer closed connection" in message:
            return True
        return False

    async def _run_code_async(self, code: str):
        cfg = get_config()
        op_timeout = cfg["sandbox"].get("e2b_operation_timeout_seconds", 120)
        async with self._concurrency_limit:
            if self._sandbox is None:
                self._sandbox = self._sandbox_factory()
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._sandbox.run_code, code),
                    timeout=op_timeout,
                )
            except asyncio.TimeoutError:
                log.error("sandbox.operation_timeout timeout={}s", op_timeout)
                raise TimeoutError(f"E2B operation timed out after {op_timeout}s")
            except Exception as exc:
                if self._should_recreate(exc):
                    log.warning("sandbox.recreate reason=not_found")
                    self._reset_sandbox()
                    return await asyncio.wait_for(
                        asyncio.to_thread(self._sandbox.run_code, code),
                        timeout=op_timeout,
                    )
                raise

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

    async def delete_paper(self, arxiv_id: str) -> None:
        """Delete the paper's .tex file and any artifacts from E2B disk."""
        path = self._paper_cache.pop(arxiv_id, None)
        import re
        safe_id = re.sub(r'[^A-Za-z0-9._-]+', '_', arxiv_id)
        
        # Clean up both the resolved paper and any potential leftovers in /tmp
        cleanup_cmd = f"rm -f {path or ''} /tmp/{safe_id}.src"
        rm_dir_cmd = f"rm -rf /tmp/{safe_id}_extract"
        
        try:
            await self.run_shell(f"{cleanup_cmd} && {rm_dir_cmd}")
            log.debug("delete.done arxiv_id={}", arxiv_id)
        except Exception as exc:
            log.warning("delete.failed arxiv_id={} error={}", arxiv_id, exc)

    def close(self) -> None:
        if self._sandbox is not None:
            self._sandbox.kill()
            self._sandbox = None
