import asyncio
from collections.abc import Callable, Collection

import pytest

from cache_service.caching import TransformationCache
from cache_service.transformer import Transformer, TransformerError
from tests.fakes import CountingTransformer, InMemoryTransformationStore


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


class GatedStore(InMemoryTransformationStore):
    """Counts lookups and holds every save until released."""

    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0
        self.saving = False
        self.release = asyncio.Event()
        self._changed = asyncio.Condition()

    async def get_many(self, sources: Collection[str]) -> dict[str, str]:
        async with self._changed:
            self.lookups += 1
            self._changed.notify_all()
        return await super().get_many(sources)

    async def save(self, source: str, result: str) -> None:
        async with self._changed:
            self.saving = True
            self._changed.notify_all()
        await self.release.wait()
        await super().save(source, result)

    async def wait_for(self, predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(1), self._changed:
            await self._changed.wait_for(predicate)


def caching(
    transformer: Transformer,
    store: InMemoryTransformationStore | None = None,
    *,
    timeout: float = 1.0,
    max_concurrency: int = 10,
) -> TransformationCache:
    return TransformationCache(
        transformer,
        store if store is not None else InMemoryTransformationStore(),
        timeout=timeout,
        max_concurrency=max_concurrency,
    )


async def test_only_missing_strings_are_transformed_and_stored() -> None:
    store = InMemoryTransformationStore({"a": "cached a"})
    transformer = CountingTransformer()

    result = await caching(transformer, store).transform_many({"a", "b"})

    assert result == {"a": "cached a", "b": "B"}
    assert transformer.calls == {"b": 1}
    assert store.results == {"a": "cached a", "b": "B"}


async def test_concurrent_misses_of_one_string_share_a_single_call() -> None:
    transformer = GatedTransformer()
    cached = caching(transformer)

    requests = [
        asyncio.create_task(cached.transform_many(sources))
        for sources in ({"a", "b"}, {"a"}, {"a", "c"})
    ]
    await transformer.wait_until_in_flight(3)
    transformer.release.set()

    assert await asyncio.gather(*requests) == [
        {"a": "A", "b": "B"},
        {"a": "A"},
        {"a": "A", "c": "C"},
    ]
    assert sorted(transformer.calls) == ["a", "b", "c"]


async def test_call_is_shared_until_its_result_is_stored() -> None:
    # A request that misses the cache while the result is being written must
    # wait for that write instead of calling the transformer again.
    store = GatedStore()
    transformer = CountingTransformer()
    cached = caching(transformer, store)

    first = asyncio.create_task(cached.transform_many({"a"}))
    await store.wait_for(lambda: store.saving)
    # Checked directly: the second request's re-check of the cache could hide
    # a call that was released too early.
    assert "a" in cached._in_flight
    second = asyncio.create_task(cached.transform_many({"a"}))
    await store.wait_for(lambda: store.lookups == 3)
    store.release.set()

    assert await first == await second == {"a": "A"}
    assert transformer.calls == {"a": 1}


async def test_result_stored_elsewhere_meanwhile_is_reused() -> None:
    class FilledByAnotherReplica(InMemoryTransformationStore):
        async def get_many(self, sources: Collection[str]) -> dict[str, str]:
            found = await super().get_many(sources)
            self.results.setdefault("a", "A from another replica")
            return found

    transformer = CountingTransformer()

    result = await caching(transformer, FilledByAnotherReplica()).transform_many({"a"})

    assert result == {"a": "A from another replica"}
    assert transformer.total_calls == 0


async def test_failure_keeps_the_other_results_and_is_retried() -> None:
    store = InMemoryTransformationStore()
    transformer = CountingTransformer(fail_on={"b"})
    cached = caching(transformer, store)

    with pytest.raises(TransformerError):
        await cached.transform_many({"a", "b", "c"})
    assert store.results == {"a": "A", "c": "C"}

    transformer.fail_on.clear()
    assert await cached.transform_many({"a", "b", "c"}) == {
        "a": "A",
        "b": "B",
        "c": "C",
    }
    assert transformer.calls == {"a": 1, "b": 2, "c": 1}


async def test_failure_reaches_every_waiting_request() -> None:
    transformer = GatedTransformer(fail=True)
    cached = caching(transformer)

    requests = [asyncio.create_task(cached.transform_many({"a"})) for _ in range(2)]
    await transformer.wait_until_in_flight(1)
    transformer.release.set()
    outcomes = await asyncio.gather(*requests, return_exceptions=True)

    assert all(isinstance(outcome, TransformerError) for outcome in outcomes)
    assert transformer.calls == ["a"]


async def test_cancelled_request_still_stores_the_result() -> None:
    store = InMemoryTransformationStore()
    transformer = GatedTransformer()
    cached = caching(transformer, store)

    request = asyncio.create_task(cached.transform_many({"a"}))
    await transformer.wait_until_in_flight(1)
    request.cancel()
    transformer.release.set()

    with pytest.raises(asyncio.CancelledError):
        await request
    assert await cached.transform_many({"a"}) == {"a": "A"}
    assert store.results == {"a": "A"}
    assert transformer.calls == ["a"]


async def test_drain_waits_for_calls_nobody_waits_for_any_more() -> None:
    store = InMemoryTransformationStore()
    transformer = GatedTransformer()
    cached = caching(transformer, store)

    request = asyncio.create_task(cached.transform_many({"a"}))
    await transformer.wait_until_in_flight(1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    transformer.release.set()
    await cached.drain()

    assert store.results == {"a": "A"}


async def test_parallel_calls_are_capped() -> None:
    transformer = GatedTransformer()
    cached = caching(transformer, max_concurrency=2)

    request = asyncio.create_task(cached.transform_many(set("abcde")))
    await transformer.wait_until_in_flight(2)
    transformer.release.set()

    assert await request == {value: value.upper() for value in "abcde"}
    assert transformer.max_in_flight == 2


async def test_slow_call_times_out_as_transformer_error() -> None:
    cached = caching(GatedTransformer(), timeout=0.01)

    with pytest.raises(TransformerError, match="did not answer"):
        await cached.transform_many({"a"})
