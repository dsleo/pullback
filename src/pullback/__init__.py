from .models import LemmaMatch, SearchRequest, SearchResponse

try:  # Optional dependency (FastAPI) for library-only use and unit tests.
    from .api import app  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    app = None  # type: ignore

try:  # Optional heavy deps (pydantic_ai, etc.)
    from .orchestration import LibrarianOrchestrator  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    LibrarianOrchestrator = None  # type: ignore

__all__ = ["app", "LemmaMatch", "SearchRequest", "SearchResponse", "LibrarianOrchestrator"]
