"""Data access for cached transformations and payloads.

Every method runs in its own short transaction instead of one per request: the
service must not hold a database connection while it waits for the transformer,
and a stored result should be visible to concurrent requests (and to other
replicas) right away.
"""

import hashlib
import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cache_service.models import Base, CachedTransformation, Payload


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def insert_unless_exists(
    session: AsyncSession, model: type[Base], key: str
) -> postgresql.Insert | sqlite.Insert:
    """``INSERT ... ON CONFLICT (key) DO NOTHING`` for the session's database.

    Two requests may store the same row at the same moment; letting the
    database settle that race is simpler and safer than locking around it.
    """
    match dialect := session.get_bind().dialect.name:
        case "postgresql":
            return postgresql.insert(model).on_conflict_do_nothing(index_elements=[key])
        case "sqlite":
            return sqlite.insert(model).on_conflict_do_nothing(index_elements=[key])
        case _:
            raise NotImplementedError(f"unsupported database: {dialect}")


class TransformationRepository:
    """Transformer results, looked up by input string."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_many(self, sources: Collection[str]) -> dict[str, str]:
        """Return the cached result of every source that has one."""
        sources_by_hash = {content_hash(source): source for source in sources}
        if not sources_by_hash:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    CachedTransformation.source_hash, CachedTransformation.result
                ).where(CachedTransformation.source_hash.in_(sources_by_hash))
            )
            return {sources_by_hash[hash_]: result for hash_, result in rows}

    async def save(self, source: str, result: str) -> None:
        """Store a result; if the source is cached already, keep that one."""
        async with self._sessions.begin() as session:
            await session.execute(
                insert_unless_exists(
                    session, CachedTransformation, "source_hash"
                ).values(source_hash=content_hash(source), source=source, result=result)
            )


class PayloadRepository:
    """Generated payloads, deduplicated by content."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_or_create(self, output: str) -> tuple[uuid.UUID, bool]:
        """Return the id of the payload with this output, storing it if new.

        The flag tells whether the payload was created by this call.
        """
        digest = content_hash(output)
        async with self._sessions.begin() as session:
            created_id = await session.scalar(
                insert_unless_exists(session, Payload, "content_hash")
                .values(content_hash=digest, output=output)
                .returning(Payload.id)
            )
            if created_id is not None:
                return created_id, True
            # The insert only skips rows that are already committed, so the
            # existing payload is visible here.
            existing = await session.execute(
                select(Payload.id).where(Payload.content_hash == digest)
            )
            return existing.scalar_one(), False

    async def get_output(self, payload_id: uuid.UUID) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(Payload.output).where(Payload.id == payload_id)
            )
