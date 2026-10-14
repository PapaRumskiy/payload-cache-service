import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cache_service.repositories import PayloadRepository, TransformationRepository


async def test_cache_returns_only_stored_results(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cache = TransformationRepository(sessions)
    await cache.save("a", "A")
    await cache.save("b", "B")

    assert await cache.get_many(["a", "b", "c"]) == {"a": "A", "b": "B"}


async def test_cache_lookup_of_nothing_is_empty(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert await TransformationRepository(sessions).get_many([]) == {}


async def test_cache_keeps_the_first_result_for_a_source(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cache = TransformationRepository(sessions)
    await cache.save("a", "A")
    await cache.save("a", "changed")

    assert await cache.get_many(["a"]) == {"a": "A"}


async def test_cache_handles_long_and_non_ascii_sources(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cache = TransformationRepository(sessions)
    long_source = "x" * 10_000
    await cache.save(long_source, "long")
    await cache.save("привіт ✓", "ПРИВІТ ✓")

    assert await cache.get_many([long_source, "привіт ✓"]) == {
        long_source: "long",
        "привіт ✓": "ПРИВІТ ✓",
    }


async def test_concurrent_saves_of_one_source_keep_one_result(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    cache = TransformationRepository(sessions)

    await asyncio.gather(*(cache.save("a", f"A{index}") for index in range(8)))

    assert (await cache.get_many(["a"]))["a"] in {f"A{index}" for index in range(8)}


async def test_same_output_gets_the_same_payload_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payloads = PayloadRepository(sessions)

    first_id, first_created = await payloads.get_or_create("A, B")
    second_id, second_created = await payloads.get_or_create("A, B")
    other_id, _ = await payloads.get_or_create("C, D")

    assert (first_created, second_created) == (True, False)
    assert second_id == first_id
    assert other_id != first_id


async def test_concurrent_creates_of_one_output_agree_on_the_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payloads = PayloadRepository(sessions)

    results = await asyncio.gather(*(payloads.get_or_create("A, B") for _ in range(8)))

    assert len({payload_id for payload_id, _ in results}) == 1
    assert [created for _, created in results].count(True) == 1


async def test_payload_output_is_read_back_by_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payloads = PayloadRepository(sessions)
    payload_id, _ = await payloads.get_or_create("A, B")

    assert await payloads.get_output(payload_id) == "A, B"
    assert await payloads.get_output(uuid.uuid4()) is None
