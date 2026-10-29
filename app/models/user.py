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


# --- Entidad de Dominio: Usuario del Sistema ---
class User(BaseModel):
    username: str = Field(
        ...,
        examples=["B14", "U28", "guardia1", "admin"],
        description="Identificador de usuario (para residentes es su lote ej. B14)",
    )
    password_hash: str = Field(..., exclude=True)
    role: UserRole
    lot_id: str | None = Field(
        default=None,
        examples=["B14"],
        description="Código de lote asociado (solo para RESIDENT; None para GUARD y ADMIN)",
    )
    is_active: bool = True
    last_login_at: datetime | None = None
    last_login_ip: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Contratos HTTP de Autenticación y Seguridad ---
class LoginRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=2,
        max_length=30,
        examples=["B14"],
        description="Usuario o código de lote (ej. B14, U28, guardia1, admin)",
    )
    password: str = Field(
        ...,
        min_length=4,
        max_length=100,
        examples=["VC3-B140"],
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_days: int = 30
    username: str
    role: UserRole
    lot_id: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=4, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)


class BlockedIPReport(BaseModel):
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
