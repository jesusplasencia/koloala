"""pin_requests: registro (solo INSERT) de solicitudes de PIN por WhatsApp para limitar envíos por número

Revision ID: 0003_pin_requests
Revises: 0002_attendee_doc_source
Create Date: 2026-11-15 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_pin_requests"
down_revision: str | None = "0002_attendee_doc_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pin_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pin_requests_phone", "pin_requests", ["phone"])
    op.create_index("ix_pin_requests_requested_at", "pin_requests", ["requested_at"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'koloala_runtime') THEN
                GRANT SELECT, INSERT ON pin_requests TO koloala_runtime;
                REVOKE UPDATE, DELETE, TRUNCATE ON pin_requests FROM koloala_runtime;
                GRANT USAGE, SELECT ON SEQUENCE pin_requests_id_seq TO koloala_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_pin_requests_requested_at", table_name="pin_requests")
    op.drop_index("ix_pin_requests_phone", table_name="pin_requests")
    op.drop_table("pin_requests")
