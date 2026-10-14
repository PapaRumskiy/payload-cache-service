"""Payload generation."""

from collections.abc import Sequence

OUTPUT_SEPARATOR = ", "


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
