"""Hybrid sandbox: uses local cached papers, falls back to E2B for uncached."""

from __future__ import annotations

from pathlib import Path

from ..observability import get_logger, trace_span
from .base import SandboxRunner
from .e2b import E2BSandboxRunner
from .local import LocalSandboxRunner

log = get_logger("sandbox.hybrid")


class HybridSandboxRunner:
    """Tries LocalSandboxRunner first for cached papers, falls back to E2B."""

    def __init__(self, cache_dir: str | Path | None = None, *, e2b_runner: E2BSandboxRunner | None = None):
        # Resolve cache_dir: use provided, or default to project root/data/benchmark_papers_tex
        if cache_dir is None:
            # Resolve to project root dynamically
            cache_dir = Path(__file__).resolve().parents[3] / "data" / "benchmark_papers_tex"
        else:
            cache_dir = Path(cache_dir)

        self.cache_dir = cache_dir
        self._cached_papers: dict[str, Path] = {}
        self._load_cached_papers()
        self._local_runner = LocalSandboxRunner(paper_map=self._cached_papers) if self._cached_papers else None
        self._e2b_runner = e2b_runner  # Lazy-load E2B only when needed
        self._e2b_initialized = False

        if self._cached_papers:
            log.info("hybrid.init cached_papers={} from cache_dir={}", len(self._cached_papers), self.cache_dir)

    def _load_cached_papers(self) -> None:
        """Scan cache directory for .tex files."""
        for tex_file in self.cache_dir.glob("*.tex"):
            # Map filename back to arXiv ID: "alg-geom_9503007.tex" -> "alg-geom/9503007"
            arxiv_id = tex_file.stem.replace("_", "/")
            self._cached_papers[arxiv_id] = tex_file

    def _ensure_e2b(self) -> E2BSandboxRunner:
        """Lazy-load E2B sandbox on first use."""
        if not self._e2b_initialized:
            if self._e2b_runner is None:
                log.info("hybrid.e2b_init lazy-initializing E2B sandbox")
                self._e2b_runner = E2BSandboxRunner.create()
            self._e2b_initialized = True
        return self._e2b_runner

    def _command_uses_local_path(self, command: str) -> bool:
        """Check if command references a locally-cached paper path."""
        return self._local_runner is not None and any(
            str(path) in command for path in self._cached_papers.values()
        )

    async def run_shell(self, command: str) -> str:
        """Route to local runner if command references a cached paper, else E2B."""
        with trace_span("sandbox.hybrid.run_shell"):
            if self._command_uses_local_path(command):
                assert self._local_runner is not None
                try:
                    return await self._local_runner.run_shell(command)
                except Exception as exc:
                    log.warning("run_shell.local_failed falling_back_to_e2b command={} error={}", command[:100], type(exc).__name__)
                    e2b = self._ensure_e2b()
                    return await e2b.run_shell(command)

            # Use E2B for non-cached commands
            e2b = self._ensure_e2b()
            return await e2b.run_shell(command)

    async def resolve_paper_path(self, arxiv_id: str) -> str:
        """Resolve paper path: check cache first, then E2B."""
        with trace_span("sandbox.hybrid.resolve_paper_path", arxiv_id=arxiv_id):
            # Check if we have it cached
            if arxiv_id in self._cached_papers:
                path = str(self._cached_papers[arxiv_id])
                log.info("resolve.local_cache arxiv_id={} path={}", arxiv_id, path)
                return path

            # Fall back to E2B
            log.info("resolve.fallback_e2b arxiv_id={} (not in cache)", arxiv_id)
            try:
                e2b = self._ensure_e2b()
                path = await e2b.resolve_paper_path(arxiv_id)
                log.info("resolve.e2b_success arxiv_id={}", arxiv_id)
                return path
            except Exception as exc:
                log.error("resolve.e2b_failed arxiv_id={} error_type={} error={}", arxiv_id, type(exc).__name__, exc)
                raise

    async def delete_paper(self, arxiv_id: str) -> None:
        """Delete paper from E2B disk after processing to free space. No-op for cached papers."""
        if arxiv_id in self._cached_papers:
            return  # locally cached — don't delete
        if self._e2b_initialized and self._e2b_runner is not None:
            await self._e2b_runner.delete_paper(arxiv_id)

    def close(self) -> None:
        if self._e2b_initialized and self._e2b_runner is not None:
            self._e2b_runner.close()
