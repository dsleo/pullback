import asyncio

import pydantic_ai.models as pyd_models
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pullback.discovery import DiscoveryAccessError
from pullback.models import LemmaMatch
from pullback.orchestration import LibrarianOrchestrator


class _Discovery:
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query
        return ["2401.00001", "2401.00002"][:max_results]


class _Forager:
    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        _ = query, strictness
        return LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=10,
            header_line="\\begin{lemma}[Banach]",
            snippet="Let X be complete.",
            score=0.9,
        )


class _FailingDiscovery:
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query, max_results
        raise DiscoveryAccessError("openalex: timed out")


def test_query_planner_supports_test_model_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=_Discovery(),
        forager=_Forager(),
        model_name="openai:gpt-5-mini",
        agentic=True,
        max_query_attempts=3,
        timeout_seconds=1.0,
    )

    with orchestrator.query_planner_agent.override(
        model=TestModel(custom_output_args={"queries": ["banach contraction", "non reflexive fixed point"]})
    ):
        attempts = asyncio.run(orchestrator._query_attempts("banach fixed point theorem"))

    assert attempts == [
        "banach fixed point theorem",
        "banach contraction",
        "non reflexive fixed point",
    ]


def test_function_model_can_drive_plain_agent_output() -> None:
    async def _echo_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[TextPart("synthetic-response")])

    agent = Agent(FunctionModel(_echo_model), output_type=str)
    result = asyncio.run(agent.run("hello"))
    assert result.output == "synthetic-response"


def test_query_planner_falls_back_when_model_requests_are_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    previous = pyd_models.ALLOW_MODEL_REQUESTS
    pyd_models.ALLOW_MODEL_REQUESTS = False
    try:
        orchestrator = LibrarianOrchestrator(
            discovery_client=_Discovery(),
            forager=_Forager(),
            model_name="openai:gpt-5-mini",
            agentic=True,
            max_query_attempts=2,
            timeout_seconds=0.5,
        )
        attempts = asyncio.run(orchestrator._query_attempts("banach fixed point theorem"))
        assert attempts[0] == "banach fixed point theorem"
        assert len(attempts) >= 1
    finally:
        pyd_models.ALLOW_MODEL_REQUESTS = previous


def test_discovery_calls_client_directly() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=_Discovery(),
        forager=_Forager(),
        model_name="test",
        agentic=True,
        max_query_attempts=1,
    )
    discovered = asyncio.run(orchestrator._discover_arxiv_ids("banach", 2))
    assert discovered == ["2401.00001", "2401.00002"]


def test_discovery_returns_empty_ids_when_client_raises() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=_FailingDiscovery(),
        forager=_Forager(),
        model_name="test",
        agentic=True,
        max_query_attempts=1,
    )
    discovered = asyncio.run(orchestrator._discover_arxiv_ids("banach", 2))
    assert discovered == []
