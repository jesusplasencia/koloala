import re
from datetime import datetime
from urllib.parse import quote

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.models.user import MembershipRole


class LotType(StrEnum):
    LOT = "LOT"  # Lote residencial (casa)
    MINI_LOT = "MINI_LOT"  # MiniLote comercial (Panadería, Botica, etc.)
    COMMON_AREA = "COMMON_AREA"  # Área común / municipal (canchas, parques)


def normalize_villa_id(raw_villa_id: str) -> str:
    """Normaliza el identificador de la Villa (ej. 'vc3' -> 'VC3', 'vc2' -> 'VC2')."""
    cleaned = raw_villa_id.strip().upper()
    if not re.fullmatch(r"VC\d+", cleaned):
        return cleaned
    return cleaned


def normalize_lot_id(raw_lot_id: str) -> str:
    """
    Normaliza el código de lote o identificador de usuario:
    - Si es un teléfono móvil peruano con prefijo o espacios (ej. '+51 916 220 518' o '916 220 518') -> '916220518'
    - Si es un lote corto (ej. 'b-4' o 'p 20') -> 'B04', 'P20'
    - Si es un usuario de sistema (ej. 'guardia1', 'admin') -> 'GUARDIA1', 'ADMIN'
    """
    cleaned = raw_lot_id.strip().upper()
    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) == 9 and digits_only.startswith("9") and re.fullmatch(r"[\d\s\-\+]+", cleaned):
        return digits_only
    if (
        len(digits_only) == 11
        and digits_only.startswith("519")
        and re.fullmatch(r"[\d\s\-\+]+", cleaned)
    ):
        return digits_only[2:]

    match = re.fullmatch(r"([A-Z])\s*-?\s*(\d{1,2})", cleaned)
    if match:
        letter, number = match.groups()
        return f"{letter}{int(number):02d}"
    return cleaned


def normalize_phone_username(raw_phone: str | None) -> str | None:
    """
    Normaliza un celular peruano al estándar local de 9 dígitos usado como username ('916220518').
    Acepta '916 220 518', '+51 916220518', '51916220518' -> '916220518'.
    """
    if not raw_phone:
        return None
    digits = re.sub(r"\D", "", raw_phone)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("519"):
        return digits[2:]
    return digits


def normalize_peru_phone(raw_phone: str | None) -> str | None:
    """
    Limpia el número de celular (ej. '987 654 321' o '+51987654321' -> '51987654321')
    para generar enlaces directos de WhatsApp (wa.me) y despachos por Evolution API.
    """
    if not raw_phone:
        return None
    digits = re.sub(r"\D", "", raw_phone)
    if not digits:
        return None
    if len(digits) == 9 and digits.startswith("9"):
        return f"51{digits}"
    return digits


def build_whatsapp_onboarding_url(
    phone: str | None,
    villa_id: str,
    lot_id: str,
    password: str,
    login_url: str,
) -> str | None:
    norm_phone = normalize_peru_phone(phone)
    local_phone = normalize_phone_username(phone) or lot_id
    if not norm_phone:
        return None
    message = (
        f"Hola vecino del lote {lot_id} ({villa_id}). "
        f"Ya está activo tu acceso rápido a garita en {login_url} | "
        f"Usuario (Celular): {local_phone} (Lote {lot_id}) | Contraseña: {password}"
    )
    return f"https://wa.me/{norm_phone}?text={quote(message)}"


# --- Entidad de Dominio: Lote Físico con Llave Compuesta (villa_id, lot_id) ---
class PropertyLot(BaseModel):
    villa_id: str = Field(
        default="VC3",
        examples=["VC1", "VC2", "VC3"],
        description="Identificador de la Villa / Etapa (parte 1 de la llave compuesta)",
    )
    lot_id: str = Field(
        ...,
        examples=["B14", "U28", "P20"],
        description="Identificador corto Manzana y Lote (parte 2 de la llave compuesta)",
    )
    owner_name: str = Field(default="Propietario")
    owner_phone: str | None = Field(
        default=None,
        examples=["987654321"],
        description="Celular (WhatsApp) de la Persona-Ancla (ANCHOR) principal",
    )
    owner_email: str | None = Field(
        default=None,
        examples=["vecino.p20@gmail.com"],
        description="Correo electrónico del propietario (opcional)",
    )
    is_maintenance_up_to_date: bool
    lot_type: LotType = Field(
        default=LotType.LOT,
        description="LOT (casa), MINI_LOT (negocio) o COMMON_AREA (cancha/parque municipal)",
    )
    display_name: str | None = Field(
        default=None,
        examples=["A01 - Panadería", "Canchita de Fútbol 1"],
        description="Nombre visible en el catálogo público de la Villa",
    )
    is_public: bool = Field(
        default=False,
        description="True si aparece en el catálogo público (vitrina) de la garita",
    )
    created_at: datetime
    updated_at: datetime

    @field_validator("villa_id", mode="before")
    @classmethod
    def _validate_villa_id(cls, value: str) -> str:
        return normalize_villa_id(value)

    @field_validator("lot_id", mode="before")
    @classmethod
    def _validate_lot_id(cls, value: str) -> str:
        return normalize_lot_id(value)

    @property
    def composite_key(self) -> tuple[str, str]:
        return (self.villa_id, self.lot_id)


# --- Entidad 3NF: Membresía Relacional Persona <-> Lote (villa_id, lot_id, phone) ---
class LotMembership(BaseModel):
    villa_id: str = Field(
        default="VC3",
        examples=["VC1", "VC2", "VC3"],
        description="Villa a la que pertenece el lote",
    )
    lot_id: str = Field(
        ...,
        examples=["P20", "Q02", "Q03"],
        description="Código de lote físico (Foreign Key hacia property_lots)",
    )
    phone: str = Field(
        ...,
        examples=["916220518", "980180219"],
        description="Número de celular (9 dígitos) que identifica a la persona",
    )
    full_name: str = Field(
        ...,
        examples=["Jesús Plasencia", "Angel Plasencia", "Pepito Juárez"],
        description="Nombre completo de la Persona-Ancla o Conviviente",
    )
    document_id: str | None = Field(
        default=None,
        examples=["72345678"],
        description="DNI (8 dígitos) o Carnet de Extranjería (9-12 dígitos)",
    )
    membership_role: MembershipRole = Field(
        default=MembershipRole.ANCHOR,
        description="ANCHOR (Persona-Ancla / Titular) o HOUSEHOLD (Conviviente / Familiar)",
    )
    added_by: str = Field(
        default="ADMIN",
        description="Teléfono o usuario que registró la membresía (ej. '934253226', '916220518')",
    )
    is_active: bool = True
    is_maintenance_up_to_date: bool | None = Field(
        default=None,
        description="Estado de mantenimiento del lote asociado (enriquecido al consultar)",
    )
    created_at: datetime
    updated_at: datetime

    @field_validator("villa_id", mode="before")
    @classmethod
    def _validate_villa_id(cls, value: str) -> str:
        return normalize_villa_id(value)

    @field_validator("lot_id", mode="before")
    @classmethod
    def _validate_lot_id(cls, value: str) -> str:
        return normalize_lot_id(value)

    @field_validator("phone", mode="before")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        return normalize_phone_username(value) or value.strip()

    @property
    def composite_key(self) -> tuple[str, str, str]:
        return (self.villa_id, self.lot_id, self.phone)


class AddHouseholdMemberRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=9,
        max_length=20,
        examples=["980180219"],
        description="Celular de 9 dígitos del conviviente / invitado frecuente",
    )
    full_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        examples=["Angel Plasencia"],
        description="Nombre completo del conviviente (ej. 'Angel Plasencia')",
    )
    document_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=15,
        examples=["71234567"],
        description="Opcional: DNI (8 dígitos) o Carnet de Extranjería",
    )
    membership_role: MembershipRole = Field(
        default=MembershipRole.HOUSEHOLD,
        description="Por defecto HOUSEHOLD. Solo ADMIN/SUPER_ADMIN puede asignar ANCHOR.",
    )


# --- Contratos HTTP (Request / Response) para el Administrador de cada Villa ---
class UpdateLotMaintenanceRequest(BaseModel):
    is_maintenance_up_to_date: bool = Field(
        ..., description="True si pagó su cuota del mes, False si entró en mora"
    )
    owner_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Opcional: actualizar el nombre de la Persona-Ancla",
    )
    owner_phone: str | None = Field(
        default=None,
        max_length=30,
        description="Opcional: actualizar el celular/WhatsApp de la Persona-Ancla",
    )
    owner_email: str | None = Field(
        default=None,
        max_length=150,
        description="Opcional: actualizar el correo electrónico del propietario",
    )


class BulkLotItem(BaseModel):
    lot_id: str = Field(
        ...,
        min_length=2,
        max_length=10,
        examples=["B14", "U28", "P20"],
        description="Identificador Manzana y Lote (ej. B14, U28, P20)",
    )
    owner_name: str = Field(
        default="Propietario",
        min_length=2,
        max_length=100,
        examples=["Jesus Plasencia"],
    )
    owner_phone: str | None = Field(
        default=None,
        max_length=30,
        examples=["987654321"],
        description="Celular de WhatsApp de la Persona-Ancla (ANCHOR)",
    )
    owner_email: str | None = Field(
        default=None,
        max_length=150,
        examples=["vecino.p20@gmail.com"],
        description="Opcional: correo electrónico del propietario",
    )
    is_maintenance_up_to_date: bool

    @field_validator("lot_id", mode="before")
    @classmethod
    def _validate_lot_id(cls, value: str) -> str:
        return normalize_lot_id(value)


class BulkUpsertLotsRequest(BaseModel):
    lots: list[BulkLotItem] = Field(
        ...,
        min_length=1,
        description="Lista de lotes de la Villa a sincronizar (crea nuevos o actualiza existentes)",
    )


class GeneratedLotCredential(BaseModel):
    villa_id: str
    lot_id: str
    username: str
    owner_name: str = "Propietario"
    owner_phone: str | None = None
    owner_email: str | None = None
    initial_password: str = Field(
        ...,
        examples=["48291057"],
        description="Contraseña numérica de 8 dígitos autogenerada (fácil para adultos mayores y segura con bloqueo de IP)",
    )
    login_url: str = Field(
        ...,
        examples=["https://vc3.lasvillas.club"],
        description="URL del portal correspondiente a la Villa del vecino",
    )
    whatsapp_share_url: str | None = Field(
        default=None,
        description="Link directo wa.me con mensaje pre-armado si el lote cuenta con owner_phone",
    )


class BulkUpsertLotsResponse(BaseModel):
    villa_id: str
    processed_count: int
    created_count: int
    updated_count: int
    lots: list[PropertyLot]
    generated_credentials: list[GeneratedLotCredential] = Field(
        default_factory=list,
        description="Credenciales iniciales generadas únicamente para los lotes nuevos creados",
    )


class PaginatedLotsResponse(BaseModel):
    villa_id: str
    items: list[PropertyLot]
    total: int = Field(..., description="Total de lotes registrados en la Villa")
    page: int = Field(..., ge=0, description="Índice de página actual (0-indexed)")
    limit: int = Field(
        ..., ge=1, le=100, description="Cantidad máxima de lotes por página"
    )
    total_pages: int = Field(
        ..., ge=0, description="Cantidad total de páginas disponibles"
    )
    has_next: bool = Field(
        ..., description="True si existen más páginas después de la actual"
    )
