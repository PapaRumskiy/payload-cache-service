import uuid

import pytest

from cache_service.service import PayloadService
from cache_service.transformer import TransformerError
from tests.fakes import (
    CountingTransformer,
    InMemoryPayloads,
    InMemoryTransformationCache,
)

SPEC_LIST_1 = ["first string", "second string", "third string"]
SPEC_LIST_2 = ["other string", "another string", "last string"]


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer()


@pytest.fixture
def cache() -> InMemoryTransformationCache:
    return InMemoryTransformationCache()


@pytest.fixture
def payloads() -> InMemoryPayloads:
    return InMemoryPayloads()


@pytest.fixture
def service(
    cache: InMemoryTransformationCache,
    payloads: InMemoryPayloads,
    transformer: CountingTransformer,
) -> PayloadService:
    return PayloadService(cache, payloads, transformer)


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
    calls_after_first = transformer.total_calls

    second_id, created = await service.create_payload(SPEC_LIST_1, SPEC_LIST_2)

    assert (second_id, created) == (first_id, False)
    assert transformer.total_calls == calls_after_first == 6


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


async def test_cached_results_are_used_instead_of_calling_transformer(
    payloads: InMemoryPayloads, transformer: CountingTransformer
) -> None:
    cache = InMemoryTransformationCache({"a": "from cache"})
    service = PayloadService(cache, payloads, transformer)

    payload_id, _ = await service.create_payload(["a"], ["b"])

    assert await service.get_output(payload_id) == "from cache, B"
    assert transformer.calls == {"b": 1}
    assert cache.results == {"a": "from cache", "b": "B"}


async def test_requests_producing_the_same_output_share_the_id(
    service: PayloadService,
) -> None:
    lower_id, _ = await service.create_payload(["a"], ["b"])
    upper_id, created = await service.create_payload(["A"], ["B"])

    assert (upper_id, created) == (lower_id, False)


async def test_unknown_payload_has_no_output(service: PayloadService) -> None:
    assert await service.get_output(uuid.uuid4()) is None


async def test_failed_call_fails_the_request_but_keeps_finished_results(
    cache: InMemoryTransformationCache,
    payloads: InMemoryPayloads,
) -> None:
    transformer = CountingTransformer(fail_on={"b"})
    service = PayloadService(cache, payloads, transformer)

    with pytest.raises(TransformerError):
        await service.create_payload(["a", "b"], ["c", "d"])
    assert cache.results == {"a": "A", "c": "C", "d": "D"}
    assert payloads.ids_by_output == {}

    transformer.fail_on.clear()
    await service.create_payload(["a", "b"], ["c", "d"])
    assert transformer.calls == {"a": 1, "b": 2, "c": 1, "d": 1}
