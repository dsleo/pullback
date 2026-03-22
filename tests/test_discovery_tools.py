import asyncio

from mathgent.tools import DiscoveryTools


class _Provider:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query
        return self._ids[:max_results]


def test_discovery_tools_route_to_configured_clients() -> None:
    chain = _Provider(["2401.00001", "2401.00002"])
    openalex = _Provider(["2501.00003"])
    exa = _Provider(["2601.00004"])
    tools = DiscoveryTools(chain=chain, openalex=openalex, exa=exa)

    assert asyncio.run(tools.discover("banach", 2)) == ["2401.00001", "2401.00002"]
    assert asyncio.run(tools.discover_openalex("banach", 2)) == ["2501.00003"]
    assert asyncio.run(tools.discover_exa("banach", 2)) == ["2601.00004"]


def test_discovery_tools_return_empty_when_provider_not_configured() -> None:
    chain = _Provider(["2401.00001"])
    tools = DiscoveryTools(chain=chain, openalex=None, exa=None)

    assert asyncio.run(tools.discover_openalex("banach", 2)) == []
    assert asyncio.run(tools.discover_exa("banach", 2)) == []
