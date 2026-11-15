"""Initial schema (identity reorder): people (UUID) + staff_roles + property_lots + lot_memberships,
IP radar, group visit passes, attendees, resident entry events, data access log and PIN requests.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-15 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
UUID = sa.Uuid()


def upgrade() -> None:
    # --- Personas: 1 fila por ser humano. El celular es único pero NO es la PK ---
    op.create_table(
        "people",
        sa.Column("id", UUID, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("full_name", sa.String(100), nullable=True),
        sa.Column("document_id", sa.String(20), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("access_reset_at", TS, nullable=True),
        sa.Column("last_login_at", TS, nullable=True),
        sa.Column("last_login_ip", sa.String(45), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_people_phone", "people", ["phone"], unique=True)

    # --- Personal de cada Villa (ADMIN / GUARD) ---
    op.create_table(
        "staff_roles",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("person_id", UUID, sa.ForeignKey("people.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("villa_id", "person_id"),
    )

    # --- Lotes físicos y catálogo (sin datos del dueño) ---
    op.create_table(
        "property_lots",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("lot_id", sa.String(15), nullable=False),
        sa.Column("is_maintenance_up_to_date", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lot_type", sa.String(20), nullable=False, server_default="LOT"),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("villa_id", "lot_id"),
    )

    # --- Persona <-> Lote (ANCHOR / HOUSEHOLD) ---
    op.create_table(
        "lot_memberships",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("lot_id", sa.String(15), nullable=False),
        sa.Column("person_id", UUID, sa.ForeignKey("people.id"), nullable=False),
        sa.Column("membership_role", sa.String(20), nullable=False, server_default="ANCHOR"),
        sa.Column("added_by_person_id", UUID, sa.ForeignKey("people.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["villa_id", "lot_id"],
            ["property_lots.villa_id", "property_lots.lot_id"],
            name="fk_lot_memberships_property_lot",
        ),
        sa.PrimaryKeyConstraint("villa_id", "lot_id", "person_id"),
    )
    op.create_index("ix_lot_memberships_person_id", "lot_memberships", ["person_id"])

    # --- Radar anti fuerza bruta ---
    op.create_table(
        "ip_security_records",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempted_usernames", sa.JSON(), nullable=False),
        sa.Column("known_successful_users", sa.JSON(), nullable=False),
        sa.Column("blocked_at", TS, nullable=True),
        sa.Column("last_user_agent", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("villa_id", "ip_address"),
        sa.UniqueConstraint("villa_id", "ip_address", name="uq_villa_ip"),
    )

    # --- Pases grupales sin cuenta (GUEST / TAXI / DELIVERY) ---
    op.create_table(
        "visit_passes",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("short_code", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False, server_default="GUEST"),
        sa.Column("destination_lot_id", sa.String(15), nullable=False),
        sa.Column("destination_type", sa.String(20), nullable=False),
        sa.Column("destination_label", sa.String(100), nullable=False),
        sa.Column("host_person_id", UUID, sa.ForeignKey("people.id"), nullable=True),
        sa.Column("host_name", sa.String(100), nullable=True),
        sa.Column("host_role", sa.String(20), nullable=True),
        sa.Column("party_size_expected", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(120), nullable=True),
        sa.Column("vehicle_plate", sa.String(20), nullable=True),
        sa.Column("plate_hmac", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("valid_from", TS, nullable=False),
        sa.Column("valid_until", TS, nullable=False),
        sa.Column("checked_in_at", TS, nullable=True),
        sa.Column("checked_in_by_guard", sa.String(30), nullable=True),
        sa.Column("shadowed_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("villa_id", "token"),
        sa.UniqueConstraint("villa_id", "short_code", name="uq_visit_passes_short_code"),
    )
    op.create_index("ix_visit_passes_destination_lot_id", "visit_passes", ["destination_lot_id"])
    op.create_index("ix_visit_passes_plate_hmac", "visit_passes", ["plate_hmac"])
    op.create_index("ix_visit_passes_checked_in_at", "visit_passes", ["checked_in_at"])
    op.create_index("ix_visit_passes_villa_created", "visit_passes", ["villa_id", "created_at"])

    op.create_table(
        "pass_attendees",
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("attendee_no", sa.Integer(), nullable=False),
        sa.Column("first_names", sa.String(80), nullable=False),
        sa.Column("last_names", sa.String(80), nullable=True),
        sa.Column("doc_type", sa.String(15), nullable=False, server_default="NINGUNO"),
        sa.Column("doc_number", sa.String(20), nullable=True),
        sa.Column("doc_hmac", sa.String(64), nullable=True),
        sa.Column("doc_last2", sa.String(2), nullable=True),
        sa.Column("doc_source", sa.String(10), nullable=True),
        sa.Column("verification", sa.String(15), nullable=True),
        sa.Column("vouched_by_no", sa.Integer(), nullable=True),
        sa.Column("checked_in_at", TS, nullable=True),
        sa.Column("checked_in_by_guard", sa.String(30), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("shadowed_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["villa_id", "token"],
            ["visit_passes.villa_id", "visit_passes.token"],
            name="fk_pass_attendees_visit_pass",
        ),
        sa.PrimaryKeyConstraint("villa_id", "token", "attendee_no"),
    )
    op.create_index("ix_pass_attendees_doc_hmac", "pass_attendees", ["doc_hmac"])

    # --- Ingresos de residentes (solo INSERT) ---
    op.create_table(
        "entry_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("person_id", UUID, sa.ForeignKey("people.id"), nullable=False),
        sa.Column("person_name", sa.String(100), nullable=False),
        sa.Column("lots", sa.JSON(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("guard", sa.String(30), nullable=False),
        sa.Column("vehicle_plate", sa.String(20), nullable=True),
        sa.Column("plate_hmac", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entry_events_villa_id", "entry_events", ["villa_id"])
    op.create_index("ix_entry_events_at", "entry_events", ["at"])
    op.create_index("ix_entry_events_person_id", "entry_events", ["person_id"])
    op.create_index("ix_entry_events_plate_hmac", "entry_events", ["plate_hmac"])

    # --- Bitácora de acceso a datos personales (solo INSERT) ---
    op.create_table(
        "data_access_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("actor", sa.String(30), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_access_log_villa_id", "data_access_log", ["villa_id"])

    # --- Solicitudes de PIN por WhatsApp (solo INSERT) ---
    op.create_table(
        "pin_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("villa_id", sa.String(15), nullable=False),
        sa.Column("person_id", UUID, sa.ForeignKey("people.id"), nullable=False),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requested_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pin_requests_person_id", "pin_requests", ["person_id"])
    op.create_index("ix_pin_requests_requested_at", "pin_requests", ["requested_at"])

    # --- Mínimo privilegio del rol runtime (si existe): nunca DELETE ---
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'koloala_runtime') THEN
                GRANT SELECT, INSERT, UPDATE ON
                    people, staff_roles, property_lots, lot_memberships, ip_security_records,
                    visit_passes, pass_attendees
                TO koloala_runtime;
                GRANT SELECT, INSERT ON entry_events, data_access_log, pin_requests TO koloala_runtime;
                REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM koloala_runtime;
                REVOKE UPDATE ON entry_events, data_access_log, pin_requests FROM koloala_runtime;
                GRANT USAGE, SELECT ON SEQUENCE
                    entry_events_id_seq, data_access_log_id_seq, pin_requests_id_seq
                TO koloala_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for table in (
        "pin_requests",
        "data_access_log",
        "entry_events",
        "pass_attendees",
        "visit_passes",
        "ip_security_records",
        "lot_memberships",
        "property_lots",
        "staff_roles",
        "people",
    ):
        op.drop_table(table)
