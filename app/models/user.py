from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    GUARD = "GUARD"
    RESIDENT = "RESIDENT"
    VISITOR = "VISITOR"


class MembershipRole(StrEnum):
    ANCHOR = "ANCHOR"
    HOUSEHOLD = "HOUSEHOLD"


class AttackType(StrEnum):
    TARGETED_BRUTE_FORCE = "TARGETED_BRUTE_FORCE"
    PASSWORD_SPRAYING = "PASSWORD_SPRAYING"


# --- Entidad de Dominio: Usuario / Persona Identificada por Teléfono o Código ---
class User(BaseModel):
    villa_id: str = Field(
        default="VC3",
        examples=["VC1", "VC2", "VC3"],
        description="Villa primaria o ámbito del usuario (VC1..VC5)",
    )
    username: str = Field(
        ...,
        examples=["916220518", "934253226", "980180219", "B14", "GUARDIA1", "ADMIN"],
        description="Celular de 9 dígitos (identidad global en lasvillas.club) o usuario de sistema/lote",
    )
    password_hash: str = Field(..., exclude=True)
    role: UserRole
    lot_id: str | None = Field(
        default=None,
        examples=["P20", "B14"],
        description="Lote principal activo o preseleccionado (None para GUARD, ADMIN o VISITOR puro)",
    )
    full_name: str | None = Field(
        default=None,
        examples=["Jesús Plasencia", "Angel Plasencia"],
        description="Nombre completo de la persona",
    )
    document_id: str | None = Field(
        default=None,
        examples=["72345678", "001234567"],
        description="DNI (8 dígitos) o Carnet de Extranjería (9-12 dígitos)",
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
        examples=["916220518", "934253226", "P20"],
        description="Número telefónico de 9 dígitos (o usuario/código de lote)",
    )
    password: str = Field(
        ...,
        min_length=4,
        max_length=100,
        examples=["48291057"],
    )


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
        description="Lista de lotes donde este teléfono es ANCHOR o HOUSEHOLD activo en la Villa actual",
    )


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
        examples=["916220518", "980180219", "P21", "ADMIN"],
        description="Celular de 9 dígitos (ej. 916220518) o código de lote/usuario",
    )


class UserLookupStatus(StrEnum):
    READY_FOR_PASSWORD = "READY_FOR_PASSWORD"
    NEEDS_PHONE_ACTIVATION = "NEEDS_PHONE_ACTIVATION"
    UNREGISTERED_OFFER_VISITOR = "UNREGISTERED_OFFER_VISITOR"


class UserLookupResponse(BaseModel):
    villa_id: str
    username: str
    status: UserLookupStatus
    owner_name: str | None = None
    role: UserRole | None = None
    membership_role: MembershipRole | None = None
    available_lots: list[str] = Field(default_factory=list)
    visitor_onboarding_message: str | None = Field(
        default=None,
        description="Mensaje de onboarding VISITOR (nunca expone teléfonos de Admin ni residentes)",
    )
    available_services: list[dict[str, str]] = Field(
        default_factory=list,
        description="Servicios/comercios internos visitables en esta Villa (modo VISITOR)",
    )


class ActivateLotPinRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=2,
        max_length=30,
        examples=["980180219", "P21"],
        description="Número de celular o lote a activar por primera vez",
    )
    phone: str = Field(
        ...,
        min_length=6,
        max_length=30,
        examples=["980180219", "987654321"],
        description="Confirmación del número de WhatsApp donde recibirá su PIN de 8 dígitos",
    )


class ActivateLotPinResponse(BaseModel):
    villa_id: str
    lot_id: str
    delivery_channel: str
    message: str


class RegisterVisitorRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=9,
        max_length=20,
        examples=["955443322"],
        description="Celular de 9 dígitos del visitante o cliente de comercio interno",
    )
    full_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        examples=["Carlos Mendoza"],
        description="Nombre y apellido del visitante para trazabilidad en garita",
    )
    document_id: str = Field(
        ...,
        min_length=8,
        max_length=12,
        examples=["45891230", "001592831"],
        description="DNI (8 dígitos) o Carnet de Extranjería (9 a 12 caracteres)",
    )
    destination: str | None = Field(
        default=None,
        max_length=60,
        examples=["PANADERIA"],
        description="[SHADOW] Servicio interno a visitar; se ignora mientras el catálogo esté deshabilitado",
    )


class RegisterVisitorResponse(BaseModel):
    villa_id: str
    username: str
    full_name: str
    document_id: str
    role: UserRole = UserRole.VISITOR
    destination: str | None = None
    delivery_channel: str
    message: str
