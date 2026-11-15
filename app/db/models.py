"""Esquema relacional de Koloala.

Identidad ordenada:
- people           -> 1 fila por ser humano (UUID). El celular es único pero NO es la PK.
- staff_roles      -> rol de personal (ADMIN / GUARD) de una persona en una Villa.
- property_lots    -> el lote físico (sin datos del dueño).
- lot_memberships  -> relación persona <-> lote (ANCHOR / HOUSEHOLD), sin nombre ni DNI.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Clase base declarativa de SQLAlchemy 2.0."""


class PersonORM(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    access_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class StaffRoleORM(Base):
    __tablename__ = "staff_roles"

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("people.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PropertyLotORM(Base):
    """Lote físico o ítem del catálogo (MiniLote / área común). El dueño se obtiene de lot_memberships."""

    __tablename__ = "property_lots"

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    is_maintenance_up_to_date: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lot_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LOT")
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class LotMembershipORM(Base):
    __tablename__ = "lot_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["villa_id", "lot_id"],
            ["property_lots.villa_id", "property_lots.lot_id"],
            name="fk_lot_memberships_property_lot",
        ),
    )

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("people.id"), primary_key=True, index=True)
    membership_role: Mapped[str] = mapped_column(String(20), nullable=False, default="ANCHOR")
    added_by_person_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("people.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class IPSecurityRecordORM(Base):
    """Radar anti fuerza bruta / password spraying por (villa, IP)."""

    __tablename__ = "ip_security_records"
    __table_args__ = (UniqueConstraint("villa_id", "ip_address", name="uq_villa_ip"),)

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempted_usernames: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    known_successful_users: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class VisitPassORM(Base):
    """QR grupal sin cuenta (invitación de residente o mesa de garita)."""

    __tablename__ = "visit_passes"
    __table_args__ = (UniqueConstraint("villa_id", "short_code", name="uq_visit_passes_short_code"),)

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(10), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(15), nullable=False, default="GUEST")
    destination_lot_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    destination_type: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_label: Mapped[str] = mapped_column(String(100), nullable=False)
    host_person_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("people.id"), nullable=True)
    # Instantánea de quién invitó y con qué rol en ese momento (auditoría)
    host_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    host_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    party_size_expected: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vehicle_plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plate_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    checked_in_by_guard: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shadowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PassAttendeeORM(Base):
    """Cada persona del grupo. Tras 90 días el doc_number se borra (UPDATE) y queda doc_hmac + doc_last2."""

    __tablename__ = "pass_attendees"
    __table_args__ = (
        ForeignKeyConstraint(
            ["villa_id", "token"],
            ["visit_passes.villa_id", "visit_passes.token"],
            name="fk_pass_attendees_visit_pass",
        ),
    )

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    attendee_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_names: Mapped[str] = mapped_column(String(80), nullable=False)
    last_names: Mapped[str | None] = mapped_column(String(80), nullable=True)
    doc_type: Mapped[str] = mapped_column(String(15), nullable=False, default="NINGUNO")
    doc_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    doc_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    doc_last2: Mapped[str | None] = mapped_column(String(2), nullable=True)
    doc_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    verification: Mapped[str | None] = mapped_column(String(15), nullable=True)
    vouched_by_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_in_by_guard: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shadowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataAccessLogORM(Base):
    """Solo INSERT: quién consultó DNIs/placas, cuándo y con qué filtro."""

    __tablename__ = "data_access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    villa_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class EntryEventORM(Base):
    """Solo INSERT: cada ingreso de un residente con su Carnet QR o manual."""

    __tablename__ = "entry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    villa_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("people.id"), nullable=False, index=True)
    person_name: Mapped[str] = mapped_column(String(100), nullable=False)
    lots: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    guard: Mapped[str] = mapped_column(String(30), nullable=False)
    vehicle_plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plate_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class PinRequestORM(Base):
    """Solo INSERT: cada solicitud de PIN por WhatsApp, para limitar envíos por persona."""

    __tablename__ = "pin_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    villa_id: Mapped[str] = mapped_column(String(15), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("people.id"), nullable=False, index=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
