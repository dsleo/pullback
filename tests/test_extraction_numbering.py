from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from mathgent.extraction.numbering import _build_label_scan_command, extract_theorem_labels_from_text


def test_extract_theorem_labels_from_text() -> None:
    latex = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{prop}{Proposition}
\numberwithin{prop}{section}
\begin{document}
\section{Intro}
\begin{theorem}
Statement.
\end{theorem}
\begin{lemma}
Statement.
\end{lemma}
\section*{Background}
\begin{lemma}
Statement.
\end{lemma}
\section{Main}
\begin{lemma}
Statement.
\end{lemma}
\begin{prop}
Statement.
\end{prop}
\end{document}
"""
    labels = extract_theorem_labels_from_text(latex)
    assert "Theorem 1.1" in labels
    assert "Lemma 1.2" in labels
    assert "Lemma 1.3" in labels
    assert "Lemma 2.1" in labels
    assert "Proposition 2.1" in labels


def test_shared_counter_counts_nonstandard_envs() -> None:
    latex = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}
\newtheorem{assumption}[theorem]{Assumption}
\begin{document}
\begin{assumption}
Statement.
\end{assumption}
\begin{theorem}
Statement.
\end{theorem}
\end{document}
"""
    labels = extract_theorem_labels_from_text(latex)
    assert "Theorem 2" in labels


def test_appendix_section_letters() -> None:
    latex = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}[section]
\begin{document}
\appendix
\section{Aux}
\begin{theorem}
Statement.
\end{theorem}
\end{document}
"""
    labels = extract_theorem_labels_from_text(latex)
    assert "Theorem A.1" in labels


def test_roman_theorem_counter() -> None:
    latex = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}
\renewcommand{\thetheorem}{\Roman{theorem}}
\begin{document}
\begin{theorem}
Statement.
\end{theorem}
\begin{theorem}
Statement.
\end{theorem}
\end{document}
"""
    labels = extract_theorem_labels_from_text(latex)
    assert "Theorem I" in labels
    assert "Theorem II" in labels


def test_label_scan_command_matches_inline_script() -> None:
    latex = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}[section]
\begin{document}
\section{Intro}
\begin{theorem}
Statement.
\end{theorem}
\end{document}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "paper.tex"
        path.write_text(latex)
        cmd = _build_label_scan_command(str(path))
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        assert proc.returncode in (0, 1)
        payload = proc.stdout.strip()
        assert payload
        data = json.loads(payload)
        assert "Theorem 1.1" in data
