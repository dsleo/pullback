#!/usr/bin/env python
"""Download arXiv source files for all papers in the benchmark dataset."""

import gzip
import json
import shutil
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

OUTPUT_DIR = Path("data/benchmark_papers_tex")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LATEX_MARKERS = (
    "\\begin{document}",
    "\\documentclass",
    "\\section",
    "\\theorem",
    "\\lemma",
    "\\begin{theorem}",
    "\\begin{lemma}",
    "\\begin{proposition}",
)


def is_latex_file(path: Path) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return False
    return any(marker in text for marker in LATEX_MARKERS)


def score_file(path: Path) -> tuple[int, int, int]:
    """Score a tex file: (theorem_count, structure_count, size)."""
    text = path.read_text(errors="ignore")
    env_hits = (
        text.count("\\begin{lemma}")
        + text.count("\\begin{proposition}")
        + text.count("\\begin{theorem}")
    )
    structure_hits = sum(
        token in text
        for token in (
            "\\begin{document}",
            "\\documentclass",
            "\\newtheorem",
            "\\section",
        )
    )
    return env_hits, structure_hits, len(text)


def sanitize_filename(arxiv_id: str) -> str:
    """Convert arXiv ID to safe filename."""
    return arxiv_id.replace("/", "_")


def download_and_extract(arxiv_id: str) -> bool:
    """Download a paper from arXiv and extract its main .tex file."""
    safe_name = sanitize_filename(arxiv_id)
    output_file = OUTPUT_DIR / f"{safe_name}.tex"
    if output_file.exists():
        print(f"✓ {arxiv_id} already cached")
        return True

    safe_id = arxiv_id.replace("/", "_")
    temp_dir = Path(f"/tmp/{safe_id}_extract")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Try arXiv mirrors
    urls = [
        f"https://export.arxiv.org/e-print/{arxiv_id}",
        f"https://arxiv.org/e-print/{arxiv_id}",
    ]

    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": "mathgent/0.1"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            break
        except HTTPError as e:
            print(f"  {url}: HTTP {e.code}")
            continue
        except Exception as e:
            print(f"  {url}: {e}")
            continue
    else:
        print(f"✗ {arxiv_id} download failed (all mirrors)")
        return False

    # Try to unpack as tarball
    try:
        with tarfile.open(fileobj=None, mode="r:*") as tar:
            tar.extractall(temp_dir)
    except (tarfile.ReadError, Exception):
        # Fallback: treat as gzip
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
        (temp_dir / f"{safe_id}.tex").write_bytes(data)

    # Find best .tex file
    tex_files = []
    for pattern in ("*.tex", "*.ltx", "*.latex"):
        tex_files.extend(temp_dir.glob(f"**/{pattern}"))

    if not tex_files:
        for p in temp_dir.rglob("*"):
            if p.is_file() and is_latex_file(p):
                tex_files.append(p)

    if not tex_files:
        print(f"✗ {arxiv_id} no .tex file found")
        return False

    # Score and select best
    best = max(tex_files, key=score_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best, output_file)
    print(f"✓ {arxiv_id} downloaded ({output_file.stat().st_size / 1024:.1f} KB)")
    shutil.rmtree(temp_dir, ignore_errors=True)
    return True


def main():
    # Load benchmark and get unique paper IDs
    with open("data/benchmark_clean_71.jsonl") as f:
        ids = sorted(set(json.loads(line)["gt_arxiv_id"] for line in f))

    print(f"Downloading {len(ids)} papers from arXiv...")
    succeeded = 0
    for arxiv_id in ids:
        if download_and_extract(arxiv_id):
            succeeded += 1

    print(f"\n{succeeded}/{len(ids)} papers cached successfully")
    # List what we have
    cached = sorted(p.stem for p in OUTPUT_DIR.glob("*.tex"))
    print(f"Cached: {len(cached)} files")


if __name__ == "__main__":
    main()
