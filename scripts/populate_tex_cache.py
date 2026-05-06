#!/usr/bin/env python3
"""
Populate tex_cache with all papers from benchmark_clean_93.jsonl
Downloads arXiv sources and caches the main .tex file locally.
"""

import json
import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests


def normalize_filename(arxiv_id: str) -> str:
    """Convert arxiv ID to cache filename (replace / with -)"""
    return arxiv_id.replace('/', '-') + '.tex'


def download_and_cache_paper(arxiv_id: str, cache_dir: Path, retry: int = 3) -> bool:
    """
    Download paper source from arXiv and cache the main .tex file.
    Returns True if successful, False otherwise.
    """
    filename = normalize_filename(arxiv_id)
    filepath = cache_dir / filename

    # Skip if already cached
    if filepath.exists():
        return True

    for attempt in range(retry):
        try:
            url = f"https://arxiv.org/src/{arxiv_id}"
            response = requests.get(url, timeout=15)

            if response.status_code == 404:
                print(f"  ✗ {arxiv_id:15} - Not found on arXiv (404)")
                return False
            elif response.status_code != 200:
                print(f"  ⚠ {arxiv_id:15} - HTTP {response.status_code} (attempt {attempt+1}/{retry})")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)  # exponential backoff
                continue

            # Extract tar.gz
            with tarfile.open(fileobj=BytesIO(response.content)) as tar:
                members = tar.getmembers()
                tex_files = [m for m in members if m.name.endswith('.tex')]

                if not tex_files:
                    print(f"  ✗ {arxiv_id:15} - No .tex files in archive")
                    return False

                # Get largest tex file (likely main)
                largest = max(tex_files, key=lambda m: m.size)
                extracted = tar.extractfile(largest)

                if not extracted:
                    print(f"  ✗ {arxiv_id:15} - Could not extract {largest.name}")
                    return False

                # Write to cache
                with open(filepath, 'wb') as f:
                    f.write(extracted.read())

                print(f"  ✓ {arxiv_id:15} → {filename}")
                return True

        except requests.Timeout:
            print(f"  ⚠ {arxiv_id:15} - Timeout (attempt {attempt+1}/{retry})")
            if attempt < retry - 1:
                time.sleep(2 ** attempt)
            continue
        except Exception as e:
            print(f"  ✗ {arxiv_id:15} - Error: {str(e)[:40]}")
            return False

    return False


def main():
    # Paths
    benchmark_path = Path(__file__).parent.parent / 'data' / 'benchmark_clean_93.jsonl'
    cache_dir = Path(__file__).parent.parent / 'data' / 'tex_cache'

    if not benchmark_path.exists():
        print(f"Error: {benchmark_path} not found")
        return False

    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load benchmark and extract unique arxiv_ids
    arxiv_ids: set[str] = set()
    with open(benchmark_path) as f:
        for line in f:
            sample = json.loads(line)
            arxiv_id = sample.get('gt_arxiv_id')
            if arxiv_id:
                arxiv_ids.add(arxiv_id)

    arxiv_ids = sorted(arxiv_ids)

    print("=" * 100)
    print(f"POPULATING TEX CACHE FROM benchmark_clean_93.jsonl")
    print("=" * 100)
    print(f"\nFound {len(arxiv_ids)} unique papers to cache\n")

    success_count = 0
    failed_count = 0

    for i, arxiv_id in enumerate(arxiv_ids, 1):
        if download_and_cache_paper(arxiv_id, cache_dir):
            success_count += 1
        else:
            failed_count += 1

        # Rate limiting
        if i % 5 == 0:
            time.sleep(1)

    print("\n" + "=" * 100)
    print(f"RESULTS")
    print("=" * 100)
    print(f"Total papers:     {len(arxiv_ids)}")
    print(f"Successfully cached: {success_count} ({100*success_count/len(arxiv_ids):.1f}%)")
    print(f"Failed:           {failed_count} ({100*failed_count/len(arxiv_ids):.1f}%)")

    # Show cache stats
    tex_files = list(cache_dir.glob("*.tex"))
    total_size_mb = sum(f.stat().st_size for f in tex_files) / (1024 * 1024)

    print(f"\nCache directory: {cache_dir}")
    print(f"Cached files:    {len(tex_files)}")
    print(f"Total size:      {total_size_mb:.1f} MB")

    return failed_count == 0


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)
