"""create cache tables

Revision ID: 2183df5f2bce
Revises:
Create Date: 2026-10-14 16:16:05.221174
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2183df5f2bce"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cached_transformations",
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_hash", name=op.f("pk_cached_transformations")),
    )
    op.create_table(
        "payloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payloads")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_payloads_content_hash")),
    )


def downgrade() -> None:
    op.drop_table("payloads")
    op.drop_table("cached_transformations")
