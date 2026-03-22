import asyncio
import time
from dataclasses import dataclass

from mathgent.sandbox.e2b import E2BSandboxRunner
from mathgent.sandbox.local import LocalSandboxRunner


@dataclass
class _FakeLogs:
    stdout: list[str]


@dataclass
class _FakeExecution:
    error: object | None
    logs: _FakeLogs


class _SlowFakeSandbox:
    def run_code(self, code: str) -> _FakeExecution:
        _ = code
        time.sleep(0.2)
        return _FakeExecution(error=None, logs=_FakeLogs(stdout=["ok"]))


def test_e2b_runner_run_shell_does_not_block_event_loop() -> None:
    runner = E2BSandboxRunner(sandbox=_SlowFakeSandbox())

    async def heartbeat() -> int:
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.04)
            ticks += 1
        return ticks

    async def run() -> tuple[str, int, float]:
        start = time.perf_counter()
        shell_task = asyncio.create_task(runner.run_shell("echo ok"))
        tick_task = asyncio.create_task(heartbeat())
        shell_out, ticks = await asyncio.gather(shell_task, tick_task)
        elapsed = time.perf_counter() - start
        return shell_out, ticks, elapsed

    out, ticks, elapsed = asyncio.run(run())
    assert out.strip() == "ok"
    assert ticks >= 4
    assert elapsed < 0.35


def test_local_runner_run_shell_scales_with_concurrency(tmp_path) -> None:
    runner = LocalSandboxRunner({"dummy": tmp_path / "dummy.tex"})
    cmd = "python -c 'import time; time.sleep(0.2); print(\"ok\")'"

    async def run_seq() -> float:
        start = time.perf_counter()
        await runner.run_shell(cmd)
        await runner.run_shell(cmd)
        return time.perf_counter() - start

    async def run_concurrent() -> float:
        start = time.perf_counter()
        await asyncio.gather(runner.run_shell(cmd), runner.run_shell(cmd))
        return time.perf_counter() - start

    seq = asyncio.run(run_seq())
    conc = asyncio.run(run_concurrent())
    assert conc < seq * 0.8
