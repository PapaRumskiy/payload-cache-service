"""Entry point of cache-cli."""

import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

import httpx
from pydantic import BaseModel, ValidationError

from cache_cli.client import CacheServiceClient, ServiceError
from cache_cli.settings import PROG, STDIO, CliSettings, describe_errors, parse_args
from cache_service.schemas import PayloadCreate

EXIT_OK = 0
EXIT_SERVICE_ERROR = 1
EXIT_USAGE_ERROR = 2

# Generous: one request may wait for many transformer calls.
HTTP_TIMEOUT = 30.0


class IterationResult(BaseModel):
    iteration: int
    id: str
    created: bool
    output: str
    create_ms: float
    read_ms: float


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse_args(argv)
    try:
        request = read_request(settings, sys.stdin)
    except OSError as error:
        return fail(f"cannot read the request: {error}", EXIT_USAGE_ERROR)
    except ValidationError as error:
        return fail(f"invalid request: {describe_errors(error)}", EXIT_USAGE_ERROR)

    try:
        with (
            httpx.Client(base_url=str(settings.host), timeout=HTTP_TIMEOUT) as http,
            open_output(settings.output_file) as output,
        ):
            results = run(CacheServiceClient(http), request, settings.repeat, output)
    except ServiceError as error:
        return fail(str(error), EXIT_SERVICE_ERROR)

    print(summarize(results), file=sys.stderr)
    return EXIT_OK


def read_request(settings: CliSettings, stdin: TextIO) -> PayloadCreate:
    """Load the request body from --json, --input FILE or stdin."""
    if settings.json_body is not None:
        raw = settings.json_body
    elif settings.input_file is None or settings.input_file == STDIO:
        raw = stdin.read()
    else:
        raw = Path(settings.input_file).read_text(encoding="utf-8")
    return PayloadCreate.model_validate_json(raw)


@contextmanager
def open_output(target: str) -> Iterator[TextIO]:
    if target == STDIO:
        yield sys.stdout
    else:
        with Path(target).open("w", encoding="utf-8") as file:
            yield file


def run(
    client: CacheServiceClient, request: PayloadCreate, repeat: int, output: TextIO
) -> list[IterationResult]:
    """Create and read back the payload ``repeat`` times, writing one line each.

    Timing every step shows the cache at work: only the first create has to
    wait for the transformer.
    """
    results = []
    for iteration in range(1, repeat + 1):
        started = time.perf_counter()
        created = client.create_payload(request)
        created_at = time.perf_counter()
        payload = client.read_payload(created.payload_id)
        finished = time.perf_counter()

        result = IterationResult(
            iteration=iteration,
            id=str(created.payload_id),
            created=created.created,
            output=payload,
            create_ms=round((created_at - started) * 1000, 1),
            read_ms=round((finished - created_at) * 1000, 1),
        )
        # Written as soon as known, so long runs can be followed live.
        output.write(result.model_dump_json() + "\n")
        output.flush()
        results.append(result)
    return results


def summarize(results: Sequence[IterationResult]) -> str:
    created = sum(result.created for result in results)
    create_ms = [result.create_ms for result in results]
    return (
        f"{len(results)} iteration(s): {created} created, "
        f"{len(results) - created} reused; create took "
        f"{min(create_ms):.1f}-{max(create_ms):.1f} ms "
        f"(median {statistics.median(create_ms):.1f} ms)"
    )


def fail(message: str, status: int) -> int:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    return status
