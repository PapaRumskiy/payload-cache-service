"""The transformer: the slow external service whose results we cache."""

import asyncio
from typing import Protocol


class TransformerError(Exception):
    """The transformer service failed or did not answer in time."""


class Transformer(Protocol):
    """Turns one string into another, typically by calling a remote service.

    Implementations raise ``TransformerError`` when the service fails, so that
    the API can tell a failing dependency apart from a bug.
    """

    async def __call__(self, value: str, /) -> str: ...


class SimulatedTransformer:
    """Stand-in for the external service: uppercases after a network-like delay.

    The delay makes the effect of caching visible when the service runs.
    """

    def __init__(self, latency: float) -> None:
        self._latency = latency

    async def __call__(self, value: str, /) -> str:
        await asyncio.sleep(self._latency)
        return value.upper()
