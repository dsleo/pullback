import asyncio

from mathgent.discovery.providers.exa import ExaDiscoveryClient


class _FakeResponse:
    def __init__(self):
        self.results = [
            {"url": "https://arxiv.org/abs/2502.12345v1"},
            {"url": "https://arxiv.org/abs/2502.12345v2"},
            {"text": "arXiv:2401.00001v1"},
        ]


class _FakeExa:
    async def search(self, *args, **kwargs):
        _ = args, kwargs
        return _FakeResponse()


def test_exa_provider_parses_and_dedupes_ids(monkeypatch) -> None:
    client = ExaDiscoveryClient(api_key="dummy")
    monkeypatch.setattr(client, "_client", lambda: _FakeExa())

    ids = asyncio.run(client.discover_arxiv_ids("banach", 3))
    assert ids == ["2502.12345", "2401.00001"]
