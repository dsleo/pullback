"""arXiv-specific helpers: ID parsing and metadata."""

from .ids import dedupe_preserve, extract_arxiv_id_from_text, normalize_arxiv_id
from .paper_metadata import PaperMetadata
try:  # Optional dependency: `arxiv` library
    from .metadata import PaperMetadataFetcher, fetch_arxiv_metadata, normalize_dedup_arxiv_ids
except ModuleNotFoundError:  # pragma: no cover
    PaperMetadataFetcher = None  # type: ignore
    fetch_arxiv_metadata = None  # type: ignore
    normalize_dedup_arxiv_ids = None  # type: ignore

__all__ = [
    "dedupe_preserve",
    "extract_arxiv_id_from_text",
    "normalize_arxiv_id",
    "normalize_dedup_arxiv_ids",
    "PaperMetadata",
    "PaperMetadataFetcher",
    "fetch_arxiv_metadata",
]
