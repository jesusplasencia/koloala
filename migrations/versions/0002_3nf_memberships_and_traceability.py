"""3NF relational schema: lot_memberships, phone identity, visitor registration, and forensic traceability

Revision ID: 0002_3nf_memberships
Revises: 0001_initial_schema
Create Date: 2026-11-10 19:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_3nf_memberships"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Tabla 3NF lot_memberships (relación N:M desacoplada entre Persona y Lotes Físicos)
    op.create_table(
        "lot_memberships",
        sa.Column("villa_id", sa.String(length=15), nullable=False),
        sa.Column("lot_id", sa.String(length=15), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("document_id", sa.String(length=20), nullable=True),
        sa.Column(
            "membership_role",
            sa.String(length=20),
            nullable=False,
            server_default="ANCHOR",
        ),
        sa.Column(
            "added_by",
            sa.String(length=30),
            nullable=False,
            server_default="ADMIN",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["villa_id", "lot_id"],
            ["property_lots.villa_id", "property_lots.lot_id"],
            name="fk_lot_memberships_property_lot",
        ),
        sa.PrimaryKeyConstraint("villa_id", "lot_id", "phone"),
    )
    op.create_index(
        op.f("ix_lot_memberships_phone"), "lot_memberships", ["phone"], unique=False
    )

    # 2. Nuevas columnas de identidad en users (full_name, document_id)
    op.add_column(
        "users", sa.Column("full_name", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "users", sa.Column("document_id", sa.String(length=20), nullable=True)
    )
    op.create_index(
        op.f("ix_users_username"), "users", ["username"], unique=False
    )

    # 3. Nuevas columnas de trazabilidad forense y bitácora diaria en guest_passes
    op.alter_column(
        "guest_passes",
        "lot_id",
        existing_type=sa.String(length=15),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    op.add_column(
        "guest_passes",
        sa.Column(
            "destination",
            sa.String(length=60),
            nullable=False,
            server_default="Lote",
        ),
    )
    op.add_column(
        "guest_passes",
        sa.Column("created_by_phone", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "guest_passes",
        sa.Column("created_by_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "guest_passes",
        sa.Column("created_by_role", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "guest_passes",
        sa.Column("checked_in_by_guard", sa.String(length=30), nullable=True),
    )

    # 4. Otorgar permisos de mínimo privilegio (SELECT, INSERT, UPDATE) a koloala_runtime si existe
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'koloala_runtime') THEN
                GRANT SELECT, INSERT, UPDATE ON TABLE lot_memberships TO koloala_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_column("guest_passes", "checked_in_by_guard")
    op.drop_column("guest_passes", "created_by_role")
    op.drop_column("guest_passes", "created_by_name")
    op.drop_column("guest_passes", "created_by_phone")
    op.drop_column("guest_passes", "destination")
    op.alter_column(
        "guest_passes",
        "lot_id",
        existing_type=sa.String(length=20),
        type_=sa.String(length=15),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_column("users", "document_id")
    op.drop_column("users", "full_name")
    op.drop_index(op.f("ix_lot_memberships_phone"), table_name="lot_memberships")
    op.drop_table("lot_memberships")
