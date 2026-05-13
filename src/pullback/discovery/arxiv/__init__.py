"""arXiv-specific helpers: ID parsing and metadata."""

from .ids import dedupe_preserve, extract_arxiv_id_from_text, normalize_arxiv_id
from .metadata import PaperMetadata, PaperMetadataFetcher, fetch_arxiv_metadata, normalize_dedup_arxiv_ids

__all__ = [
    "dedupe_preserve",
    "extract_arxiv_id_from_text",
    "normalize_arxiv_id",
    "normalize_dedup_arxiv_ids",
    "PaperMetadata",
    "PaperMetadataFetcher",
    "fetch_arxiv_metadata",
]
