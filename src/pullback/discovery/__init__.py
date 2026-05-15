from .arxiv.ids import extract_arxiv_id_from_text, normalize_arxiv_id
from .base import DiscoveryAccessError, PaperDiscoveryClient

try:
    from .providers.arxiv_api import ArxivAPIDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    ArxivAPIDiscoveryClient = None  # type: ignore

try:
    from .providers.arxiv_search_html import ArxivSearchHtmlDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    ArxivSearchHtmlDiscoveryClient = None  # type: ignore

try:
    from .providers.semantic_scholar import SemanticScholarDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    SemanticScholarDiscoveryClient = None  # type: ignore

try:
    from .providers.openalex import OpenAlexDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    OpenAlexDiscoveryClient = None  # type: ignore

try:
    from .providers.openrouter_search import OpenRouterSearchDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    OpenRouterSearchDiscoveryClient = None  # type: ignore

try:
    from .providers.web_search_arxiv import WebSearchArxivDiscoveryClient, WebSearchArxivConfig
except ModuleNotFoundError:  # pragma: no cover
    WebSearchArxivDiscoveryClient = None  # type: ignore
    WebSearchArxivConfig = None  # type: ignore

try:
    from .providers.zbmath_open import ZbMathOpenDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    ZbMathOpenDiscoveryClient = None  # type: ignore

try:  # Optional dependency: limiter + fallback modules
    from .pipeline import ChainedDiscoveryClient
except ModuleNotFoundError:  # pragma: no cover
    ChainedDiscoveryClient = None  # type: ignore

try:  # Optional dependency: `arxiv` library
    from .arxiv.metadata import PaperMetadata, PaperMetadataFetcher, fetch_arxiv_metadata
except ModuleNotFoundError:  # pragma: no cover
    PaperMetadata = None  # type: ignore
    PaperMetadataFetcher = None  # type: ignore
    fetch_arxiv_metadata = None  # type: ignore

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
    "ArxivSearchHtmlDiscoveryClient",
    "OpenRouterSearchDiscoveryClient",
    "OpenAlexDiscoveryClient",
    "ZbMathOpenDiscoveryClient",
    "SemanticScholarDiscoveryClient",
    "WebSearchArxivDiscoveryClient",
    "WebSearchArxivConfig",
]
