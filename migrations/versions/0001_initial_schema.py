"""Initial schema with composite keys and least-privilege grants

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-05 20:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "property_lots",
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("lot_id", sa.String(length=15), nullable=False),
        sa.Column("owner_name", sa.String(length=100), nullable=False),
        sa.Column("owner_phone", sa.String(length=30), nullable=True),
        sa.Column("owner_email", sa.String(length=150), nullable=True),
        sa.Column("is_maintenance_up_to_date", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("villa_id", "lot_id"),
    )

    op.create_table(
        "users",
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("username", sa.String(length=30), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("lot_id", sa.String(length=15), nullable=True),
        sa.Column("email", sa.String(length=150), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("villa_id", "username"),
    )

    op.create_table(
        "guest_passes",
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("lot_id", sa.String(length=15), nullable=False),
        sa.Column("pass_type", sa.String(length=20), nullable=False),
        sa.Column("visitor_info", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_in_dni", sa.String(length=20), nullable=True),
        sa.Column("checked_in_plate", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("villa_id", "code"),
    )
    op.create_index(
        op.f("ix_guest_passes_lot_id"), "guest_passes", ["lot_id"], unique=False
    )

    op.create_table(
        "ip_security_records",
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("attempted_usernames", sa.JSON(), nullable=False),
        sa.Column("known_successful_users", sa.JSON(), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_user_agent", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("villa_id", "ip_address"),
        sa.UniqueConstraint("villa_id", "ip_address", name="uq_villa_ip"),
    )


def downgrade() -> None:
    op.drop_table("ip_security_records")
    op.drop_index(op.f("ix_guest_passes_lot_id"), table_name="guest_passes")
    op.drop_table("guest_passes")
    op.drop_table("users")
    op.drop_table("property_lots")
