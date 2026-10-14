import asyncio

import pytest

from cache_service.transformer import (
    CoalescingTransformer,
    SimulatedTransformer,
    TransformerError,
)


class GatedTransformer:
    """Uppercases values once released, so tests control when calls finish."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.release = asyncio.Event()
        self._fail = fail
        self._started = asyncio.Condition()

    async def __call__(self, value: str, /) -> str:
        self.calls.append(value)
        async with self._started:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self._started.notify_all()
        try:
            await self.release.wait()
        finally:
            self.in_flight -= 1
        if self._fail:
            raise TransformerError("service unavailable")
        return value.upper()

    async def wait_until_in_flight(self, count: int) -> None:
        async with asyncio.timeout(1), self._started:
            await self._started.wait_for(lambda: self.in_flight >= count)


def coalescing(
    inner: GatedTransformer, *, timeout: float = 1.0, max_concurrency: int = 10
) -> CoalescingTransformer:
    return CoalescingTransformer(
        inner, timeout=timeout, max_concurrency=max_concurrency
    )


async def test_simulated_transformer_uppercases() -> None:
    assert await SimulatedTransformer(latency=0)("first string") == "FIRST STRING"


async def test_concurrent_calls_for_one_value_share_a_single_call() -> None:
    inner = GatedTransformer()
    transformer = coalescing(inner)

    callers = [asyncio.create_task(transformer("a")) for _ in range(3)]
    await inner.wait_until_in_flight(1)
    inner.release.set()

    assert await asyncio.gather(*callers) == ["A", "A", "A"]
    assert inner.calls == ["a"]


async def test_distinct_values_are_called_separately() -> None:
    inner = GatedTransformer()
    inner.release.set()
    transformer = coalescing(inner)

    first, second = await asyncio.gather(transformer("a"), transformer("b"))

    assert (first, second) == ("A", "B")
    assert sorted(inner.calls) == ["a", "b"]


async def test_finished_calls_are_not_reused() -> None:
    # Remembering results is the database cache's job, not the coalescer's.
    inner = GatedTransformer()
    inner.release.set()
    transformer = coalescing(inner)

    await transformer("a")
    await transformer("a")

    assert inner.calls == ["a", "a"]


async def test_failure_reaches_every_waiting_caller_and_is_not_kept() -> None:
    inner = GatedTransformer(fail=True)
    transformer = coalescing(inner)

    callers = [asyncio.create_task(transformer("a")) for _ in range(2)]
    await inner.wait_until_in_flight(1)
    inner.release.set()
    outcomes = await asyncio.gather(*callers, return_exceptions=True)

    assert all(isinstance(outcome, TransformerError) for outcome in outcomes)
    with pytest.raises(TransformerError):
        await transformer("a")
    assert inner.calls == ["a", "a"]


async def test_cancelled_caller_does_not_cancel_the_shared_call() -> None:
    inner = GatedTransformer()
    transformer = coalescing(inner)

    leaving = asyncio.create_task(transformer("a"))
    staying = asyncio.create_task(transformer("a"))
    await inner.wait_until_in_flight(1)
    leaving.cancel()
    inner.release.set()

    assert await staying == "A"
    with pytest.raises(asyncio.CancelledError):
        await leaving
    assert inner.calls == ["a"]


async def test_parallel_calls_are_capped() -> None:
    inner = GatedTransformer()
    transformer = coalescing(inner, max_concurrency=2)

    callers = [asyncio.create_task(transformer(value)) for value in "abcde"]
    await inner.wait_until_in_flight(2)
    for _ in range(10):
        await asyncio.sleep(0)
    assert inner.in_flight == 2

    inner.release.set()
    assert await asyncio.gather(*callers) == ["A", "B", "C", "D", "E"]
    assert inner.max_in_flight == 2


async def test_slow_call_times_out_as_transformer_error() -> None:
    inner = GatedTransformer()
    transformer = coalescing(inner, timeout=0.01)

    with pytest.raises(TransformerError, match="did not answer"):
        await transformer("a")
