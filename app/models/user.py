from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    RESIDENT = "RESIDENT"
    GUARD = "GUARD"
    ADMIN = "ADMIN"


class AttackType(StrEnum):
    TARGETED_BRUTE_FORCE = "TARGETED_BRUTE_FORCE"
    PASSWORD_SPRAYING = "PASSWORD_SPRAYING"


# --- Entidad de Dominio: Usuario Sectorizado por Villa (villa_id, username) ---
class User(BaseModel):
    villa_id: str = Field(
        default="VC3",
        examples=["VC1", "VC2", "VC3"],
        description="Villa a la que pertenece el usuario (VC1, VC2, VC3)",
    )
    username: str = Field(
        ...,
        examples=["B14", "U28", "P20", "GUARDIA1", "ADMIN"],
        description="Identificador de usuario dentro de su Villa",
    )
    password_hash: str = Field(..., exclude=True)
    role: UserRole
    lot_id: str | None = Field(
        default=None,
        examples=["B14", "P20"],
        description="Código de lote asociado (solo para RESIDENT; None para GUARD y ADMIN)",
    )
    email: str | None = None
    is_active: bool = True
    last_login_at: datetime | None = None
    last_login_ip: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def composite_key(self) -> tuple[str, str]:
        return (self.villa_id, self.username)


# --- Contratos HTTP de Autenticación y Seguridad ---
class LoginRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=2,
        max_length=30,
        examples=["B14", "P20"],
        description="Usuario o código de lote dentro de la Villa (ej. B14, P20, guardia1, admin)",
    )
    password: str = Field(
        ...,
        min_length=4,
        max_length=100,
        examples=["VC3-B14"],
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_days: int = 30
    villa_id: str
    username: str
    role: UserRole
    lot_id: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=4, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)


class BlockedIPReport(BaseModel):
    villa_id: str
    ip_address: str
    failed_attempts: int
    is_blocked: bool
    attack_type: AttackType
    attempted_usernames: list[str]
    suspected_lots_from_previous_logins: list[str] = Field(
        default_factory=list,
        description="Lotes/usuarios de la Villa que iniciaron sesión con éxito antes desde esta misma IP",
    )
    blocked_at: datetime | None = None
    last_user_agent: str | None = None


class UserLookupRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=2,
        max_length=30,
        examples=["P21", "P20", "ADMIN"],
        description="Código de lote o usuario de garita/administración",
    )


class UserLookupStatus(StrEnum):
    READY_FOR_PASSWORD = "READY_FOR_PASSWORD"
    NEEDS_PHONE_ACTIVATION = "NEEDS_PHONE_ACTIVATION"


class UserLookupResponse(BaseModel):
    villa_id: str
    username: str
    status: UserLookupStatus
    masked_phone_hint: str | None = Field(
        default=None,
        description=(
            "Últimos 3 dígitos del WhatsApp registrado (ej. '••• 321') "
            "cuando requiere activación inicial"
        ),
    )
    owner_name: str | None = None


class ActivateLotPinRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30, examples=["P21"])
    phone: str = Field(..., min_length=6, max_length=30, examples=["987654321"])


class ActivateLotPinResponse(BaseModel):
    villa_id: str
    lot_id: str
    delivery_channel: str
    message: str
