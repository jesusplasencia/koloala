"""users.token_version (cierra sesiones al restablecer acceso) y users.access_reset_at (reinicia límite de PIN)

Revision ID: 0004_users_token_version
Revises: 0003_pin_requests
Create Date: 2026-11-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_users_token_version"
down_revision: str | None = "0003_pin_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("access_reset_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "access_reset_at")
    op.drop_column("users", "token_version")
