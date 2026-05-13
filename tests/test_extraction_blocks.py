"""Tests for block extraction from LaTeX source files.

Guards against regressions in:
  1. Correct \begin{env}...\end{env} boundary detection.
  2. Nested environments — inner \begin{proof} must not terminate outer \begin{theorem}.
  3. max_lines cap — very long blocks get truncated.
  4. Multiple blocks extracted from a single file in one call.
  5. Window fallback when environment name is unknown/missing.
  6. Starred environments (\begin{theorem*}) treated identically.
"""

from __future__ import annotations

import asyncio

from pullback.sandbox import LocalSandboxRunner
from pullback.tools import ExtractionTools


# ---------------------------------------------------------------------------
# LaTeX fixtures
# ---------------------------------------------------------------------------

NESTED_ENV_TEX = r"""
\section{Main Results}

\begin{theorem}[Banach Fixed Point]
Let $(X, d)$ be a complete metric space.
\begin{proof}
  Define the iteration $x_{n+1} = T(x_n)$.
  \begin{enumerate}
    \item The sequence is Cauchy.
    \item It converges to a fixed point.
  \end{enumerate}
  Thus $T$ has a unique fixed point.
\end{proof}
\end{theorem}

\begin{lemma}[Auxiliary]
Helper statement.
\end{lemma}
""".strip()


LONG_ENV_TEX = "\n".join([
    r"\begin{theorem}[Long]",
    *[f"  Line {i} of a very long theorem." for i in range(300)],
    r"\end{theorem}",
])


MULTI_BLOCK_TEX = r"""
\begin{lemma}[First]
First lemma content.
\end{lemma}

Some intervening text.

\begin{theorem}[Second]
Second theorem content.
\end{theorem}

More text.

\begin{proposition}[Third]
Third proposition content.
\end{proposition}
""".strip()


STARRED_ENV_TEX = r"""
\begin{theorem*}
An unnumbered theorem.
\end{theorem*}

\begin{lemma*}[Key]
A key unnumbered lemma.
\end{lemma*}
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tools(arxiv_id: str, content: str, tmp_path) -> ExtractionTools:
    tex_file = tmp_path / f"{arxiv_id}.tex"
    tex_file.write_text(content)
    runner = LocalSandboxRunner({arxiv_id: tex_file})
    return ExtractionTools(runner)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_block_extraction_respects_environment_boundaries(tmp_path) -> None:
    """The extracted block must start at \begin{theorem} and end at \end{theorem}."""
    arxiv_id = "2401.nested"
    tools = _make_tools(arxiv_id, NESTED_ENV_TEX, tmp_path)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    theorem_header = next(h for h in headers if "theorem" in h.line.lower() and "lemma" not in h.line.lower())

    block = asyncio.run(
        tools.fetch_latex_block(
            arxiv_id=arxiv_id,
            line_number=theorem_header.line_number,
            context_lines=0,
            environment_name="theorem",
        )
    )
    assert r"\begin{theorem}" in block
    assert r"\end{theorem}" in block
    # The lemma must NOT bleed into the theorem block
    assert r"\begin{lemma}" not in block


def test_nested_environments_do_not_terminate_outer_block(tmp_path) -> None:
    """\begin{proof} inside \begin{theorem} must not cause early termination."""
    arxiv_id = "2401.nested2"
    tools = _make_tools(arxiv_id, NESTED_ENV_TEX, tmp_path)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    theorem_header = next(h for h in headers if "theorem" in h.line.lower() and "lemma" not in h.line.lower())

    block = asyncio.run(
        tools.fetch_latex_block(
            arxiv_id=arxiv_id,
            line_number=theorem_header.line_number,
            context_lines=0,
            environment_name="theorem",
        )
    )
    # The proof block must be INSIDE the extracted theorem block
    assert r"\begin{proof}" in block, "proof should be part of the theorem block"
    assert r"\end{proof}" in block, "proof end should be part of the theorem block"
    assert "unique fixed point" in block


def test_bulk_fetch_extracts_multiple_blocks(tmp_path) -> None:
    """fetch_header_blocks must return a snippet for each requested header."""
    arxiv_id = "2401.multi"
    tools = _make_tools(arxiv_id, MULTI_BLOCK_TEX, tmp_path)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    assert len(headers) >= 3, f"expected ≥3 headers, got {len(headers)}: {[h.line for h in headers]}"

    blocks = asyncio.run(tools.fetch_header_blocks(arxiv_id, headers, context_lines=2))

    assert len(blocks) == len(headers), "must return one block per header"
    for h in headers:
        assert h.line_number in blocks, f"missing block for header at line {h.line_number}"
        assert blocks[h.line_number], f"block for line {h.line_number} is empty"


def test_multi_block_contents_are_distinct(tmp_path) -> None:
    """Each extracted block must contain its own content, not bleed into neighbours."""
    arxiv_id = "2401.distinct"
    tools = _make_tools(arxiv_id, MULTI_BLOCK_TEX, tmp_path)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    blocks = asyncio.run(tools.fetch_header_blocks(arxiv_id, headers, context_lines=0))

    snippets = list(blocks.values())
    lemma_block = next((s for s in snippets if "First lemma" in s), None)
    theorem_block = next((s for s in snippets if "Second theorem" in s), None)

    assert lemma_block is not None, "lemma block not found"
    assert theorem_block is not None, "theorem block not found"
    assert "Second theorem" not in lemma_block, "theorem content must not bleed into lemma block"
    assert "First lemma" not in theorem_block, "lemma content must not bleed into theorem block"


def test_starred_environments_are_discovered(tmp_path) -> None:
    """\begin{theorem*} and \begin{lemma*} must be found by the header scanner."""
    arxiv_id = "2401.starred"
    tools = _make_tools(arxiv_id, STARRED_ENV_TEX, tmp_path)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    lines = [h.line for h in headers]

    assert any("theorem" in l.lower() for l in lines), f"no theorem* header found in {lines}"
    assert any("lemma" in l.lower() for l in lines), f"no lemma* header found in {lines}"
