"""The transformer: the slow external service whose results we cache."""

import asyncio
from typing import Protocol


class Transformer(Protocol):
    """Turns one string into another, typically by calling a remote service."""

    async def __call__(self, value: str, /) -> str: ...


class TransformerError(Exception):
    """The transformer service failed or did not answer in time."""


class SimulatedTransformer:
    """Stand-in for the external service: uppercases after a network-like delay.

    The delay makes the effect of caching visible when the service runs.
    """

    def __init__(self, latency: float) -> None:
        self._latency = latency

    async def __call__(self, value: str, /) -> str:
        await asyncio.sleep(self._latency)
        return value.upper()


class CoalescingTransformer:
    """Guards calls to a transformer service.

    The database cache only helps once a result has been stored. Requests that
    miss the cache at the same moment would otherwise each call the service for
    the same string, so concurrent calls for one value share a single call.
    On top of that, the number of parallel calls is capped so one large request
    cannot flood the service, and every call gets a timeout.

    Coalescing is per process. Replicas can still call the service once each for
    the same string at the same moment; the cache write is idempotent, so that
    costs an extra call but never breaks anything.
    """

    def __init__(
        self, inner: Transformer, *, timeout: float, max_concurrency: int
    ) -> None:
        self._inner = inner
        self._timeout = timeout
        self._slots = asyncio.Semaphore(max_concurrency)
        self._in_flight: dict[str, asyncio.Task[str]] = {}

    async def __call__(self, value: str, /) -> str:
        task = self._in_flight.get(value)
        if task is None:
            task = asyncio.create_task(self._call(value))
            self._in_flight[value] = task
            task.add_done_callback(lambda _: self._in_flight.pop(value, None))
        # A caller that is cancelled (e.g. its client disconnected) must not
        # cancel the call that other callers are still waiting for.
        return await asyncio.shield(task)

    async def _call(self, value: str) -> str:
        async with self._slots:
            try:
                async with asyncio.timeout(self._timeout):
                    return await self._inner(value)
            except TimeoutError as exc:
                raise TransformerError(
                    f"transformer did not answer within {self._timeout}s"
                ) from exc
