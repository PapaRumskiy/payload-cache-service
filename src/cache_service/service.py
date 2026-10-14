"""Payload generation."""

import uuid
from collections.abc import Sequence
from typing import Protocol

from cache_service.caching import TransformationCache

OUTPUT_SEPARATOR = ", "


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
    """Builds payloads from transformed strings and stores them by content."""

    def __init__(self, cache: TransformationCache, payloads: PayloadStore) -> None:
        self._cache = cache
        self._payloads = payloads

    async def create_payload(
        self, list_1: Sequence[str], list_2: Sequence[str]
    ) -> tuple[uuid.UUID, bool]:
        """Generate the payload and return its id and whether it is new.

        Payloads are identified by their content, so a request that produces an
        already generated payload gets the existing id back.
        """
        # A set, so a string repeated within the request is transformed once.
        transformed = await self._cache.transform_many({*list_1, *list_2})
        output = render_output(
            [transformed[source] for source in list_1],
            [transformed[source] for source in list_2],
        )
        return await self._payloads.get_or_create(output)

    async def get_output(self, payload_id: uuid.UUID) -> str | None:
        return await self._payloads.get_output(payload_id)
