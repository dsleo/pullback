from .arxiv.ids import extract_arxiv_id_from_text, normalize_arxiv_id
from .arxiv.metadata import PaperMetadata, PaperMetadataFetcher, fetch_arxiv_metadata
from .base import DiscoveryAccessError, PaperDiscoveryClient
from .pipeline import ChainedDiscoveryClient
from .providers.arxiv_api import ArxivAPIDiscoveryClient
from .providers.semantic_scholar import SemanticScholarDiscoveryClient
from .providers.openalex import OpenAlexDiscoveryClient
from .providers.openrouter_search import OpenRouterSearchDiscoveryClient
from .providers.zbmath_open import ZbMathOpenDiscoveryClient

__all__ = [
    "fetch_arxiv_metadata",
    "PaperMetadata",
    "PaperMetadataFetcher",
    "extract_arxiv_id_from_text",
    "normalize_arxiv_id",
    "DiscoveryAccessError",
    "PaperDiscoveryClient",
    "ChainedDiscoveryClient",
    "ArxivAPIDiscoveryClient",
    "OpenRouterSearchDiscoveryClient",
    "OpenAlexDiscoveryClient",
    "ZbMathOpenDiscoveryClient",
    "SemanticScholarDiscoveryClient",
]
