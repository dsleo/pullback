from .arxiv.ids import extract_arxiv_id_from_text, normalize_arxiv_id
from .arxiv.metadata import PaperMetadata, PaperMetadataFetcher, fetch_arxiv_metadata
from .base import DiscoveryAccessError, PaperDiscoveryClient
from .pipeline import ChainedDiscoveryClient
from .providers.openai_search import OpenAISearchDiscoveryClient
from .providers.openalex import OpenAlexDiscoveryClient

__all__ = [
    "fetch_arxiv_metadata",
    "PaperMetadata",
    "PaperMetadataFetcher",
    "extract_arxiv_id_from_text",
    "normalize_arxiv_id",
    "DiscoveryAccessError",
    "PaperDiscoveryClient",
    "ChainedDiscoveryClient",
    "OpenAISearchDiscoveryClient",
    "OpenAlexDiscoveryClient",
]
