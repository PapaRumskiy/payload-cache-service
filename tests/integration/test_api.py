import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, status
from sqlalchemy.ext.asyncio import AsyncEngine

from cache_service.config import Settings
from cache_service.main import create_app
from tests.fakes import CountingTransformer

SPEC_BODY = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
SPEC_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, "
    "ANOTHER STRING, THIRD STRING, LAST STRING"
)

type ClientFactory = Callable[
    [CountingTransformer], AbstractAsyncContextManager[httpx.AsyncClient]
]


@pytest.fixture
def transformer() -> CountingTransformer:
    # A little latency, so that concurrent requests really overlap.
    return CountingTransformer(latency=0.02)


@pytest.fixture
def start_client(engine: AsyncEngine, database_url: str) -> ClientFactory:
    """Start the app (with its lifespan) against the test database."""

    @asynccontextmanager
    async def start(
        transformer: CountingTransformer,
    ) -> AsyncIterator[httpx.AsyncClient]:
        settings = Settings(_env_file=None, database_url=database_url)
        app: FastAPI = create_app(settings, transformer=transformer)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            yield client

    return start


@pytest.fixture
async def client(
    start_client: ClientFactory, transformer: CountingTransformer
) -> AsyncIterator[httpx.AsyncClient]:
    async with start_client(transformer) as client:
        yield client


async def test_created_payload_can_be_read_back(client: httpx.AsyncClient) -> None:
    created = await client.post("/payload", json=SPEC_BODY)

    assert created.status_code == status.HTTP_201_CREATED
    body = created.json()
    assert body["message"] == "Payload created"
    assert created.headers["Location"] == f"http://test/payload/{body['id']}"

    read = await client.get(f"/payload/{body['id']}")
    assert read.status_code == status.HTTP_200_OK
    assert read.json() == {"output": SPEC_OUTPUT}


async def test_repeated_request_reuses_the_id_and_cached_results(
    client: httpx.AsyncClient, transformer: CountingTransformer
) -> None:
    first = await client.post("/payload", json=SPEC_BODY)
    second = await client.post("/payload", json=SPEC_BODY)

    assert second.status_code == status.HTTP_200_OK
    assert second.json() == {
        "id": first.json()["id"],
        "message": "Payload already exists",
    }
    assert transformer.total_calls == 6


async def test_known_strings_are_not_transformed_again(
    client: httpx.AsyncClient, transformer: CountingTransformer
) -> None:
    await client.post("/payload", json={"list_1": ["a", "b"], "list_2": ["c", "d"]})
    response = await client.post(
        "/payload", json={"list_1": ["b", "x"], "list_2": ["a", "a"]}
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert transformer.calls == dict.fromkeys(["a", "b", "c", "d", "x"], 1)


async def test_concurrent_requests_transform_each_string_once(
    client: httpx.AsyncClient, transformer: CountingTransformer
) -> None:
    bodies = [
        {"list_1": ["a", "b"], "list_2": ["c", "d"]},
        {"list_1": ["b", "c"], "list_2": ["d", "e"]},
        {"list_1": ["a", "b"], "list_2": ["c", "d"]},
    ]

    responses = await asyncio.gather(
        *(client.post("/payload", json=body) for body in bodies * 3)
    )

    assert all(response.is_success for response in responses)
    assert transformer.calls == dict.fromkeys("abcde", 1)
    assert len({response.json()["id"] for response in responses}) == 2


async def test_cache_survives_a_restart(
    start_client: ClientFactory, transformer: CountingTransformer
) -> None:
    async with start_client(transformer) as client:
        first = await client.post("/payload", json=SPEC_BODY)

    restarted_transformer = CountingTransformer()
    async with start_client(restarted_transformer) as client:
        partly_known = await client.post(
            "/payload", json={"list_1": ["first string"], "list_2": ["new string"]}
        )
        repeated = await client.post("/payload", json=SPEC_BODY)

    assert partly_known.status_code == status.HTTP_201_CREATED
    assert restarted_transformer.calls == {"new string": 1}
    assert repeated.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"list_1": ["a", "b"], "list_2": ["c"]}, id="different lengths"),
        pytest.param({"list_1": ["a"]}, id="missing list"),
        pytest.param({"list_1": [1], "list_2": ["a"]}, id="non-string item"),
        pytest.param({"list_1": [], "list_2": []}, id="empty lists"),
        pytest.param({"list_1": ["a"], "list_2": ["b"], "x": 1}, id="unknown field"),
    ],
)
async def test_invalid_body_is_rejected(
    client: httpx.AsyncClient, transformer: CountingTransformer, body: object
) -> None:
    response = await client.post("/payload", json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert transformer.total_calls == 0


async def test_unknown_payload_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/payload/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Payload not found"}


async def test_malformed_payload_id_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/payload/not-a-uuid")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_health_reports_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


async def test_health_reports_unreachable_database(tmp_path: Path) -> None:
    missing_directory = tmp_path / "missing" / "cache.db"
    settings = Settings(
        _env_file=None, database_url=f"sqlite+aiosqlite:///{missing_directory}"
    )
    app = create_app(settings, transformer=CountingTransformer())

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        response = await client.get("/health")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
