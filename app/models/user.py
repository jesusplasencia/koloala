from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    GUARD = "GUARD"
    RESIDENT = "RESIDENT"


class StaffRoleName(StrEnum):
    ADMIN = "ADMIN"
    GUARD = "GUARD"


class MembershipRole(StrEnum):
    ANCHOR = "ANCHOR"
    HOUSEHOLD = "HOUSEHOLD"


class AttackType(StrEnum):
    TARGETED_BRUTE_FORCE = "TARGETED_BRUTE_FORCE"
    PASSWORD_SPRAYING = "PASSWORD_SPRAYING"


# --- Entidades de dominio ---
class Person(BaseModel):
    """Una fila por ser humano (global a todas las Villas). El celular es único pero NO es la PK."""

    id: str = Field(..., description="UUID interno; nunca se usa el celular como llave")
    phone: str = Field(..., examples=["916220518"])
    full_name: str | None = None
    document_id: str | None = Field(default=None, description="DNI (8) o CE (9-12)")
    password_hash: str | None = Field(default=None, exclude=True)
    is_super_admin: bool = False
    is_active: bool = True
    token_version: int = 0
    access_reset_at: datetime | None = None
    last_login_at: datetime | None = None
    last_login_ip: str | None = None
    created_at: datetime
    updated_at: datetime


class StaffRole(BaseModel):
    villa_id: str
    person_id: str
    role: StaffRoleName
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


# --- Contratos HTTP de Autenticación y Seguridad ---
class LoginRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=9,
        max_length=20,
        examples=["916220518"],
        description="Celular de 9 dígitos (todos ingresan con su celular, incluido el personal)",
    )
    password: str = Field(..., min_length=4, max_length=64, examples=["48291057"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_days: int = 30
    villa_id: str
    username: str
    role: UserRole
    lot_id: str | None = None
    membership_role: MembershipRole | None = None
    full_name: str | None = None
    available_lots: list[str] = Field(
        default_factory=list,
        description="Lotes donde esta persona es ANCHOR o HOUSEHOLD activo en la Villa actual",
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=4, max_length=64)
    new_password: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="8 a 64 caracteres: letras, números, símbolos o espacios (NIST SP 800-63B)",
    )


class BlockedIPReport(BaseModel):
    villa_id: str
    ip_address: str
    failed_attempts: int
    is_blocked: bool
    attack_type: AttackType
    attempted_usernames: list[str]
    suspected_lots_from_previous_logins: list[str] = Field(
        default_factory=list,
        description="Celulares de la Villa que iniciaron sesión con éxito antes desde esta misma IP",
    )
    blocked_at: datetime | None = None
    last_user_agent: str | None = None


class UserLookupRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30, examples=["916220518"])


class UserLookupStatus(StrEnum):
    READY_FOR_PASSWORD = "READY_FOR_PASSWORD"
    NEEDS_DOCUMENT = "NEEDS_DOCUMENT"
    UNREGISTERED_NO_ACCOUNT = "UNREGISTERED_NO_ACCOUNT"


class UserLookupResponse(BaseModel):
    """Antes de autenticar NO se exponen nombre, rol ni lotes (evita enumerar vecinos)."""

    villa_id: str
    username: str
    status: UserLookupStatus
    message: str | None = None


class ActivateLotPinRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=9,
        max_length=20,
        examples=["980180219"],
        description="Celular de 9 dígitos que activa su cuenta por primera vez",
    )
    document_id: str = Field(
        ...,
        min_length=8,
        max_length=15,
        examples=["71234567"],
        description="DNI o Carnet de Extranjería registrado por el Administrador o el ANCHOR",
    )


class ActivateLotPinResponse(BaseModel):
    villa_id: str
    lot_id: str | None
    delivery_channel: str
    message: str


# --- Personal de garita y administración ---
class CreateStaffRequest(BaseModel):
    phone: str = Field(..., min_length=9, max_length=20, examples=["900000001"])
    full_name: str = Field(..., min_length=2, max_length=100, examples=["Vigilante de Prueba"])
    document_id: str | None = Field(default=None, max_length=15, examples=["40000003"])
    role: StaffRoleName = StaffRoleName.GUARD


class StaffMemberView(BaseModel):
    villa_id: str
    phone: str
    full_name: str | None
    role: StaffRoleName
    document_id: str | None = Field(default=None, description="Enmascarado (******NN)")
    is_active: bool
    has_activated: bool
