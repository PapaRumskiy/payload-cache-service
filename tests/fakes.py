"""In-memory stand-ins for the transformer service and the database."""

import asyncio
import uuid
from collections import Counter
from collections.abc import Collection, Mapping

from cache_service.transformer import TransformerError


class CountingTransformer:
    """Uppercases like the simulated service and records every call.

    Values listed in ``fail_on`` make the call fail, like an outage would.
    """

    def __init__(self, latency: float = 0, fail_on: Collection[str] = ()) -> None:
        self.calls: Counter[str] = Counter()
        self.fail_on = set(fail_on)
        self._latency = latency

    async def __call__(self, value: str, /) -> str:
        self.calls[value] += 1
        await asyncio.sleep(self._latency)
        if value in self.fail_on:
            raise TransformerError(f"cannot transform {value!r}")
        return value.upper()

    @property
    def total_calls(self) -> int:
        return self.calls.total()


class InMemoryTransformationStore:
    def __init__(self, results: Mapping[str, str] | None = None) -> None:
        self.results = dict(results or {})

    async def get_many(self, sources: Collection[str]) -> dict[str, str]:
        return {
            source: self.results[source] for source in sources if source in self.results
        }

    async def save(self, source: str, result: str) -> None:
        self.results.setdefault(source, result)


class InMemoryPayloads:
    def __init__(self) -> None:
        self.ids_by_output: dict[str, uuid.UUID] = {}

    async def get_or_create(self, output: str) -> tuple[uuid.UUID, bool]:
        if output in self.ids_by_output:
            return self.ids_by_output[output], False
        payload_id = self.ids_by_output[output] = uuid.uuid4()
        return payload_id, True

    async def get_output(self, payload_id: uuid.UUID) -> str | None:
        return next(
            (output for output, id_ in self.ids_by_output.items() if id_ == payload_id),
            None,
        )
