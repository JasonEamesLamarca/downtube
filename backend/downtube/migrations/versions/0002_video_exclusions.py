"""video exclusions (videos deleted from the library)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_exclusions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("user_id", "video_id"),
    )


def downgrade() -> None:
    op.drop_table("video_exclusions")
