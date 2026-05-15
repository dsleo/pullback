import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient
import importlib

from pullback.api.app import create_app
from pullback.discovery import DiscoveryAccessError
from pullback.models import SearchResponse, SearchResultEntry


class _GoodOrchestrator:
    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        return SearchResponse(
            query=query,
            max_results=max_results,
            strictness=strictness,
            results=[SearchResultEntry(arxiv_id="2401.00001", match=None)],
        )


class _DiscoveryFailOrchestrator:
    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        _ = query, max_results, strictness
        raise DiscoveryAccessError("upstream provider down")


def test_app_builds_orchestrator_once_per_lifespan(monkeypatch) -> None:
    app_module = importlib.import_module("pullback.api.app")
    build_calls = {"count": 0}

    def _builder(settings):
        _ = settings
        build_calls["count"] += 1
        return _GoodOrchestrator()

    monkeypatch.setattr(app_module, "build_orchestrator", _builder)
    with TestClient(create_app()) as client:
        payload = {
            "query": "Banach fixed point theorem for non-reflexive spaces",
            "max_results": 1,
            "strictness": 0.2,
        }
        first = client.post("/search", json=payload)
        second = client.post("/search", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert build_calls["count"] == 1


def test_search_endpoint_success(monkeypatch) -> None:
    app_module = importlib.import_module("pullback.api.app")

    monkeypatch.setattr(app_module, "build_orchestrator", lambda settings: _GoodOrchestrator())
    with TestClient(create_app()) as client:
        response = client.post(
            "/search",
            json={
                "query": "Banach fixed point theorem for non-reflexive spaces",
                "max_results": 1,
                "strictness": 0.2,
            },
            headers={"x-request-id": "test-req-001"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Banach fixed point theorem for non-reflexive spaces"
    assert body["results"][0]["arxiv_id"] == "2401.00001"
    assert response.headers["x-request-id"] == "test-req-001"


def test_search_endpoint_maps_discovery_errors_to_502(monkeypatch) -> None:
    app_module = importlib.import_module("pullback.api.app")

    monkeypatch.setattr(app_module, "build_orchestrator", lambda settings: _DiscoveryFailOrchestrator())
    with TestClient(create_app()) as client:
        response = client.post(
            "/search",
            json={
                "query": "Banach fixed point theorem for non-reflexive spaces",
                "max_results": 1,
                "strictness": 0.2,
            },
        )
    assert response.status_code == 502
    assert response.json()["detail"] == "provider unavailable"


class _RuntimeFailOrchestrator:
    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        _ = query, max_results, strictness
        raise RuntimeError("No LaTeX source found for 1234.56789")


def test_search_endpoint_sanitizes_runtime_errors(monkeypatch) -> None:
    app_module = importlib.import_module("pullback.api.app")

    monkeypatch.setattr(app_module, "build_orchestrator", lambda settings: _RuntimeFailOrchestrator())
    with TestClient(create_app()) as client:
        response = client.post(
            "/search",
            json={
                "query": "Banach fixed point theorem for non-reflexive spaces",
                "max_results": 1,
                "strictness": 0.2,
            },
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "source unavailable"


def test_app_closes_orchestrator_on_lifespan_exit(monkeypatch) -> None:
    app_module = importlib.import_module("pullback.api.app")
    state = {"closed": 0}

    class _ClosableOrchestrator(_GoodOrchestrator):
        def close(self) -> None:
            state["closed"] += 1

    monkeypatch.setattr(app_module, "build_orchestrator", lambda settings: _ClosableOrchestrator())

    with TestClient(create_app()) as client:
        response = client.post(
            "/search",
            json={
                "query": "Banach fixed point theorem for non-reflexive spaces",
                "max_results": 1,
                "strictness": 0.2,
            },
        )
        assert response.status_code == 200

    assert state["closed"] == 1
