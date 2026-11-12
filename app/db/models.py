from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Clase base declarativa de SQLAlchemy 2.0."""


class PropertyLotORM(Base):
    """
    Tabla 'property_lots' con Llave Primaria Compuesta (villa_id, lot_id).
    Aísla los lotes físicos de VC1..VC5 dentro de la misma tabla física.
    """

    __tablename__ = "property_lots"

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    owner_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Propietario"
    )
    owner_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    is_maintenance_up_to_date: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    lot_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LOT")
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class LotMembershipORM(Base):
    """
    Tabla relacional 3NF 'lot_memberships' con Llave Primaria Compuesta (villa_id, lot_id, phone)
    y Foreign Key Compuesta hacia property_lots(villa_id, lot_id).
    Desacopla la Persona (phone) del Lote físico (villa_id, lot_id):
    - Permite que una persona sea ANCHOR en varios lotes (ej. P20 y Q02).
    - Permite que una persona sea ANCHOR en Q03 y HOUSEHOLD en P20.
    - Cuando un ANCHOR retira a un HOUSEHOLD, se hace soft-deactivate (is_active=False)
      compatible con el rol de mínimo privilegio 'koloala_runtime' (sin DELETE).
    """

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
    phone: Mapped[str] = mapped_column(String(30), primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    membership_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ANCHOR"
    )
    added_by: Mapped[str] = mapped_column(String(30), nullable=False, default="ADMIN")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserORM(Base):
    """
    Tabla 'users' con Llave Primaria Compuesta (villa_id, username).
    Soporta SUPER_ADMIN ('916220518'), ADMIN ('934253226'), GUARD y RESIDENT.
    """

    __tablename__ = "users"

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    username: Mapped[str] = mapped_column(String(30), primary_key=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    lot_id: Mapped[str | None] = mapped_column(String(15), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class IPSecurityRecordORM(Base):
    """
    Tabla 'ip_security_records' con Llave Primaria Compuesta (villa_id, ip_address).
    Persiste el radar Anti-Fuerza Bruta y Anti-Password Spraying en PostgreSQL.
    """

    __tablename__ = "ip_security_records"
    __table_args__ = (UniqueConstraint("villa_id", "ip_address", name="uq_villa_ip"),)

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempted_usernames: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    known_successful_users: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class VisitPassORM(Base):
    """
    Tabla 'visit_passes': QR grupal sin cuenta (invitación de residente, cartel público o mesa de garita).
    El token es un secreto aleatorio (va dentro del QR); short_code (6) sirve para digitarlo a mano.
    """

    __tablename__ = "visit_passes"
    __table_args__ = (
        UniqueConstraint("villa_id", "short_code", name="uq_visit_passes_short_code"),
    )

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(10), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(15), nullable=False, default="GUEST")
    destination_lot_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    destination_type: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_label: Mapped[str] = mapped_column(String(100), nullable=False)
    host_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
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
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    checked_in_by_guard: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shadowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PassAttendeeORM(Base):
    """
    Tabla 'pass_attendees': cada persona del grupo (NOMBRES, APELLIDOS, DNI opcional).
    Tras 90 días el doc_number se borra por UPDATE y queda solo doc_hmac + doc_last2.
    """

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
    verification: Mapped[str | None] = mapped_column(String(15), nullable=True)
    vouched_by_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_in_by_guard: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shadowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataAccessLogORM(Base):
    """Tabla 'data_access_log' (solo INSERT): quién consultó DNIs/placas, cuándo y con qué filtro."""

    __tablename__ = "data_access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    villa_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class EntryEventORM(Base):
    """Tabla 'entry_events' (solo INSERT): cada ingreso de un residente con su Carnet QR o manual."""

    __tablename__ = "entry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    villa_id: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    person_phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    person_name: Mapped[str] = mapped_column(String(100), nullable=False)
    lots: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    guard: Mapped[str] = mapped_column(String(30), nullable=False)
    vehicle_plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plate_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
