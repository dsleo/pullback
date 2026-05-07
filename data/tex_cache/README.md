# Local TeX Cache Directory

This directory stores LaTeX source files from arXiv papers as a fallback when E2B sandbox is unavailable or for faster local access.

## Purpose

- **Fallback mechanism**: When E2B fails to download a paper (e.g., 404 errors), the system can still use cached local files
- **Performance**: Local file access is faster than E2B downloads
- **Offline capability**: Enable theorem extraction without external API calls

## File Naming Convention

Files must follow the arxiv ID naming convention:
- **New format**: `2310.15076.tex` (from arxiv ID `2310.15076`)
- **Old format**: `math-9905049.tex` (from arxiv ID `math/9905049`) — use hyphens instead of slashes

## How to Populate

### Manual Addition
1. Download paper source from arXiv: https://arxiv.org/src/{arxiv_id}
2. Extract the tar.gz file
3. Find the main .tex file (usually largest)
4. Copy to this directory with correct name

### Automated Download Script
```bash
# Download and cache a specific paper
python3 << 'EOF'
import tarfile
import requests
from io import BytesIO
from pathlib import Path

def cache_paper(arxiv_id):
    """Download paper from arXiv and cache locally"""
    url = f"https://arxiv.org/src/{arxiv_id}"
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Failed to download {arxiv_id}: HTTP {response.status_code}")
        return False
    
    try:
        with tarfile.open(fileobj=BytesIO(response.content)) as tar:
            members = tar.getmembers()
            tex_files = [m for m in members if m.name.endswith('.tex')]
            
            if not tex_files:
                print(f"No .tex files found in {arxiv_id}")
                return False
            
            # Get largest tex file (likely main)
            largest = max(tex_files, key=lambda m: m.size)
            extracted = tar.extractfile(largest)
            
            # Save with normalized filename
            filename = arxiv_id.replace('/', '-') + '.tex'
            filepath = Path(__file__).parent / filename
            
            with open(filepath, 'wb') as f:
                f.write(extracted.read())
            
            print(f"✓ Cached {arxiv_id} → {filename}")
            return True
    except Exception as e:
        print(f"✗ Error caching {arxiv_id}: {e}")
        return False

# Example usage
cache_paper("2310.15076")
cache_paper("math/9905049")
EOF
```

## System Configuration

The cache is enabled via environment variable in `.env.local`:
```bash
MATHGENT_LOCAL_TEX_DIR=data/tex_cache
```

If not set, the system falls back to E2B sandbox (requires E2B API key and credits).

## Current Status

- **Directory**: `data/tex_cache/`
- **Configured in**: `.env.local` as `MATHGENT_LOCAL_TEX_DIR`
- **Files cached**: 0 (add as needed)

## Integration

When configured:
1. Benchmark runs first check this directory for papers
2. If paper found, uses local .tex file (fast)
3. If paper not found, falls back to E2B sandbox (slower)
4. If both unavailable, paper skips with error

This provides a graceful degradation when external services are unavailable.
