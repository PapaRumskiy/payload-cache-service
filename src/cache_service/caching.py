"""Transforming strings through the cache, at most once per distinct string."""

import asyncio
import logging
from collections.abc import Collection
from typing import Protocol

from cache_service.transformer import Transformer, TransformerError

logger = logging.getLogger(__name__)


class TransformationStore(Protocol):
    async def get_many(self, sources: Collection[str]) -> dict[str, str]: ...

    async def save(self, source: str, result: str) -> None: ...


class CachingTransformer:
    """Calls the transformer only for strings that are not cached yet.

    A cache miss is resolved by one task that checks the cache again, calls the
    transformer and stores the result. Concurrent requests that miss the same
    string wait for that task instead of calling the transformer themselves.
    The task stays registered until the result is stored, which leaves no gap in
    which a string is neither in flight nor cached.

    That guarantee holds within one process. Replicas that miss the same string
    at the same moment may each call the transformer once; the second result is
    discarded when it is stored.

    Parallel calls are capped and each call has a timeout, as for any client of
    a remote service.
    """

    def __init__(
        self,
        transformer: Transformer,
        store: TransformationStore,
        *,
        timeout: float,
        max_concurrency: int,
    ) -> None:
        self._transformer = transformer
        self._store = store
        self._timeout = timeout
        self._slots = asyncio.Semaphore(max_concurrency)
        self._in_flight: dict[str, asyncio.Task[str]] = {}

    async def transform_many(self, sources: Collection[str]) -> dict[str, str]:
        """Return the transformed value of every source.

        Raises:
            TransformerError: if the transformer failed for any source. The
                results for the other sources are stored anyway, so a retry
                only repeats the calls that failed.
        """
        cached = await self._store.get_many(sources)
        missing = [source for source in sources if source not in cached]
        outcomes = await asyncio.gather(
            *map(self._resolve, missing), return_exceptions=True
        )
        errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if errors:
            logger.warning(
                "%d of %d strings could not be transformed, first error: %r",
                len(errors),
                len(missing),
                errors[0],
            )
            raise errors[0]

        logger.info(
            "Transformed %d distinct strings: %d cached, %d missing",
            len(sources),
            len(cached),
            len(missing),
        )
        resolved = {
            source: outcome
            for source, outcome in zip(missing, outcomes, strict=True)
            if isinstance(outcome, str)
        }
        return cached | resolved

    async def _resolve(self, source: str) -> str:
        task = self._in_flight.get(source)
        if task is None:
            task = asyncio.create_task(self._transform_and_store(source))
            self._in_flight[source] = task
            task.add_done_callback(lambda _: self._in_flight.pop(source, None))
        # A request that is cancelled (server shutdown, an outer timeout) must
        # not cancel the call that other requests are waiting for.
        return await asyncio.shield(task)

    async def _transform_and_store(self, source: str) -> str:
        async with self._slots:
            # Another request, or another replica, may have stored the result
            # since the caller's cache lookup.
            if cached := await self._store.get_many([source]):
                return cached[source]
            try:
                async with asyncio.timeout(self._timeout):
                    result = await self._transformer(source)
            except TimeoutError as exc:
                raise TransformerError(
                    f"transformer did not answer within {self._timeout}s"
                ) from exc
        # The slot only limits calls to the transformer; the write does not
        # need to hold it.
        await self._store.save(source, result)
        return result
