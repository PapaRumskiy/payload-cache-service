"""Payload generation."""

import asyncio
import logging
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Protocol

from cache_service.transformer import Transformer

logger = logging.getLogger(__name__)

OUTPUT_SEPARATOR = ", "


class TransformationStore(Protocol):
    async def get_many(self, sources: Collection[str]) -> dict[str, str]: ...

    async def save_many(self, results: Mapping[str, str]) -> None: ...


class PayloadStore(Protocol):
    async def get_or_create(self, output: str) -> tuple[uuid.UUID, bool]: ...

    async def get_output(self, payload_id: uuid.UUID) -> str | None: ...


def interleave[T](first: Sequence[T], second: Sequence[T]) -> list[T]:
    """Return ``[first[0], second[0], first[1], second[1], ...]``.

    Raises:
        ValueError: if the sequences differ in length. Silently dropping the
            tail of the longer one would produce a wrong payload.
    """
    return [item for pair in zip(first, second, strict=True) for item in pair]


def render_output(first: Sequence[str], second: Sequence[str]) -> str:
    """Join the interleaved transformed strings into the payload text."""
    return OUTPUT_SEPARATOR.join(interleave(first, second))


class PayloadService:
    """Builds payloads, calling the transformer only for strings it never saw."""

    def __init__(
        self,
        cache: TransformationStore,
        payloads: PayloadStore,
        transformer: Transformer,
    ) -> None:
        self._cache = cache
        self._payloads = payloads
        self._transformer = transformer

    async def create_payload(
        self, list_1: Sequence[str], list_2: Sequence[str]
    ) -> tuple[uuid.UUID, bool]:
        """Generate the payload and return its id and whether it is new.

        Payloads are identified by their content, so a request that produces an
        already generated payload gets the existing id back.
        """
        transformed = await self._transform({*list_1, *list_2})
        output = render_output(
            [transformed[source] for source in list_1],
            [transformed[source] for source in list_2],
        )
        return await self._payloads.get_or_create(output)

    async def get_output(self, payload_id: uuid.UUID) -> str | None:
        return await self._payloads.get_output(payload_id)

    async def _transform(self, sources: set[str]) -> dict[str, str]:
        """Map every source to its transformed value.

        Taking a set means a string repeated within the request is transformed
        once; the cache covers strings seen in earlier requests.
        """
        cached = await self._cache.get_many(sources)
        missing = [source for source in sources if source not in cached]
        fresh = await self._call_transformer(missing) if missing else {}
        logger.info(
            "Transformed %d distinct strings: %d from cache, %d computed",
            len(sources),
            len(cached),
            len(fresh),
        )
        return cached | fresh

    async def _call_transformer(self, sources: Sequence[str]) -> dict[str, str]:
        """Transform cache misses concurrently and cache the results."""
        results = await asyncio.gather(*map(self._transformer, sources))
        fresh = dict(zip(sources, results, strict=True))
        await self._cache.save_many(fresh)
        return fresh
