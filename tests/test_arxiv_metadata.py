import asyncio
import time

from mathgent.discovery import PaperMetadata, fetch_arxiv_metadata
import mathgent.discovery.arxiv.metadata as arxiv_metadata


def test_fetch_arxiv_metadata_dedupes_ids(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch_sync(arxiv_ids: list[str], *, max_results_per_query: int) -> dict[str, PaperMetadata]:
        _ = max_results_per_query
        calls.append(arxiv_ids)
        return {
            arxiv_id: PaperMetadata(title=f"T:{arxiv_id}", authors=["A"])
            for arxiv_id in arxiv_ids
        }

    monkeypatch.setattr(arxiv_metadata, "fetch_arxiv_metadata_sync", fake_fetch_sync)
    result = asyncio.run(fetch_arxiv_metadata(["2501.00001v2", "2501.00001", "math/9302208"]))

    assert list(result) == ["2501.00001", "math/9302208"]
    assert calls == [["2501.00001", "math/9302208"]]


def test_fetch_arxiv_metadata_returns_empty_on_failure(monkeypatch) -> None:
    def fake_fetch_sync(arxiv_ids: list[str], *, max_results_per_query: int) -> dict[str, PaperMetadata]:
        _ = arxiv_ids, max_results_per_query
        raise RuntimeError("network down")

    monkeypatch.setattr(arxiv_metadata, "fetch_arxiv_metadata_sync", fake_fetch_sync)
    result = asyncio.run(fetch_arxiv_metadata(["2501.00001"]))

    assert result == {}


def test_fetch_arxiv_metadata_returns_empty_on_timeout(monkeypatch) -> None:
    def fake_fetch_sync(arxiv_ids: list[str], *, max_results_per_query: int) -> dict[str, PaperMetadata]:
        _ = arxiv_ids, max_results_per_query
        time.sleep(0.05)
        return {"2501.00001": PaperMetadata(title="slow", authors=["A"])}

    monkeypatch.setattr(arxiv_metadata, "fetch_arxiv_metadata_sync", fake_fetch_sync)
    result = asyncio.run(fetch_arxiv_metadata(["2501.00001"], timeout_seconds=0.01))

    assert result == {}
