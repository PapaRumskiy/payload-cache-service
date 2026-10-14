"""Database tables."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Hex-encoded SHA-256 digest.
HASH_LENGTH = 64


class Base(DeclarativeBase):
    # Deterministic constraint names, so migrations can refer to them on
    # every database backend.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class CachedTransformation(Base):
    """The transformer's result for one input string."""

    __tablename__ = "cached_transformations"

    # Keyed by a hash rather than the text itself, so index entries stay small
    # however long the input is (Postgres refuses B-tree entries over ~2.7 kB).
    source_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Payload(Base):
    """A generated payload, stored once per distinct content."""

    __tablename__ = "payloads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Unique, so the same content always resolves to the same payload id.
    content_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), unique=True)
    output: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
