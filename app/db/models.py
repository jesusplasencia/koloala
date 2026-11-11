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
    Soporta SUPER_ADMIN ('916220518'), ADMIN ('934253226'), GUARD, RESIDENT y VISITOR.
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


class GuestPassORM(Base):
    """
    Tabla 'guest_passes' con Llave Primaria Compuesta (villa_id, code).
    Guarda el ciclo de vida del código/QR, la trazabilidad de quién lo creó
    (created_by_phone, created_by_name, created_by_role) y el cierre del vigilante en garita.
    """

    __tablename__ = "guest_passes"

    villa_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    pass_type: Mapped[str] = mapped_column(String(20), nullable=False)
    visitor_info: Mapped[str] = mapped_column(String(100), nullable=False)
    destination: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Lote"
    )
    created_by_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_in_dni: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checked_in_plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checked_in_by_guard: Mapped[str | None] = mapped_column(String(30), nullable=True)


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
