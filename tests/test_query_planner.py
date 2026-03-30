import asyncio

from pydantic_ai.usage import UsageLimits

from mathgent.orchestration.query_planner import QueryPlannerService


def test_query_attempts_disabled_is_base_only() -> None:
    planner = QueryPlannerService(
        model_name="test",
        enabled=False,
        max_query_attempts=3,
        timeout_seconds=0.0,
        usage_limits=UsageLimits(request_limit=1),
    )
    attempts = asyncio.run(planner.query_attempts("Banach fixed point theorem"))
    assert attempts == ["Banach fixed point theorem"]


def test_query_attempts_enabled_failure_falls_back_to_base(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    planner = QueryPlannerService(
        model_name="openai:gpt-5-mini",
        enabled=True,
        max_query_attempts=3,
        timeout_seconds=0.0,
        usage_limits=UsageLimits(request_limit=2),
    )

    async def fake_run_plan(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(planner, "_run_plan", fake_run_plan)
    attempts = asyncio.run(planner.query_attempts("compact operators on banach spaces"))
    assert attempts[0] == "compact operators on banach spaces"
