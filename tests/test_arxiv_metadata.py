import asyncio

from mathgent.discovery import ArxivMetadataClient, PaperMetadata


def test_arxiv_metadata_client_dedupes_ids(monkeypatch) -> None:
    client = ArxivMetadataClient()

    calls: list[list[str]] = []

    def fake_fetch_sync(arxiv_ids: list[str]) -> dict[str, PaperMetadata]:
        calls.append(arxiv_ids)
        return {
            arxiv_id: PaperMetadata(title=f"T:{arxiv_id}", authors=["A"])
            for arxiv_id in arxiv_ids
        }

    monkeypatch.setattr(client, "_fetch_sync", fake_fetch_sync)
    result = asyncio.run(client.fetch_metadata(["2501.00001v2", "2501.00001", "math/9302208"]))

    assert list(result) == ["2501.00001", "math/9302208"]
    assert calls == [["2501.00001", "math/9302208"]]


def test_arxiv_metadata_client_returns_empty_on_failure(monkeypatch) -> None:
    client = ArxivMetadataClient()

    def fake_fetch_sync(arxiv_ids: list[str]) -> dict[str, PaperMetadata]:
        _ = arxiv_ids
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_fetch_sync", fake_fetch_sync)
    result = asyncio.run(client.fetch_metadata(["2501.00001"]))

    assert result == {}
