import uuid

import pytest

from cache_service.caching import CachingTransformer
from cache_service.service import PayloadService
from tests.fakes import (
    CountingTransformer,
    InMemoryPayloads,
    InMemoryTransformationStore,
)

SPEC_LIST_1 = ["first string", "second string", "third string"]
SPEC_LIST_2 = ["other string", "another string", "last string"]


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer()


@pytest.fixture
def service(transformer: CountingTransformer) -> PayloadService:
    caching_transformer = CachingTransformer(
        transformer, InMemoryTransformationStore(), timeout=1, max_concurrency=10
    )
    return PayloadService(caching_transformer, InMemoryPayloads())


async def test_builds_the_spec_example(service: PayloadService) -> None:
    payload_id, created = await service.create_payload(SPEC_LIST_1, SPEC_LIST_2)

    assert created
    assert await service.get_output(payload_id) == (
        "FIRST STRING, OTHER STRING, SECOND STRING, "
        "ANOTHER STRING, THIRD STRING, LAST STRING"
    )


async def test_repeated_request_reuses_id_without_calling_transformer(
    service: PayloadService, transformer: CountingTransformer
) -> None:
    first_id, _ = await service.create_payload(SPEC_LIST_1, SPEC_LIST_2)
    second_id, created = await service.create_payload(SPEC_LIST_1, SPEC_LIST_2)

    assert (second_id, created) == (first_id, False)
    assert transformer.total_calls == 6


async def test_only_unseen_strings_are_transformed(
    service: PayloadService, transformer: CountingTransformer
) -> None:
    await service.create_payload(["a", "b"], ["c", "d"])
    await service.create_payload(["a", "x"], ["c", "y"])

    assert transformer.calls == dict.fromkeys(["a", "b", "c", "d", "x", "y"], 1)


async def test_repeated_strings_in_one_request_are_transformed_once(
    service: PayloadService, transformer: CountingTransformer
) -> None:
    payload_id, _ = await service.create_payload(["a", "a", "b"], ["b", "a", "a"])

    assert transformer.calls == {"a": 1, "b": 1}
    assert await service.get_output(payload_id) == "A, B, A, A, B, A"


async def test_requests_producing_the_same_output_share_the_id(
    service: PayloadService,
) -> None:
    lower_id, _ = await service.create_payload(["a"], ["b"])
    upper_id, created = await service.create_payload(["A"], ["B"])

    assert (upper_id, created) == (lower_id, False)


async def test_unknown_payload_has_no_output(service: PayloadService) -> None:
    assert await service.get_output(uuid.uuid4()) is None
