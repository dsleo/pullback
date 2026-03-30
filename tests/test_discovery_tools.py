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
    tools = DiscoveryTools(chain=chain)

    assert asyncio.run(tools.discover("banach", 2)) == ["2401.00001", "2401.00002"]
