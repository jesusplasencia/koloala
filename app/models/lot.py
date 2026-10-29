import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def normalize_villa_id(raw_villa_id: str) -> str:
    """Normaliza el identificador de la Villa (ej. 'vc3' -> 'VC3', 'vc2' -> 'VC2')."""
    cleaned = raw_villa_id.strip().upper()
    if not re.fullmatch(r"VC\d+", cleaned):
        return cleaned
    return cleaned


def normalize_lot_id(raw_lot_id: str) -> str:
    """
    Normaliza el código de lote al estándar corto: 'B14', 'U28', 'P20', 'F08'.
    """
    cleaned = raw_lot_id.strip().upper()
    match = re.fullmatch(r"([A-Z])\s*-?\s*(\d{1,2})", cleaned)
    if match:
        letter, number = match.groups()
        return f"{letter}{int(number):02d}"
    return cleaned


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
    owner_email: str | None = Field(
        default=None,
        examples=["vecino.p20@gmail.com"],
        description="Correo electrónico del propietario para envío de accesos y notificaciones",
    )
    is_maintenance_up_to_date: bool
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


# --- Contratos HTTP (Request / Response) para el Administrador de cada Villa ---
class UpdateLotMaintenanceRequest(BaseModel):
    is_maintenance_up_to_date: bool = Field(
        ..., description="True si pagó su cuota del mes, False si entró en mora"
    )
    owner_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Opcional: actualizar el nombre del propietario",
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
    owner_email: str | None = Field(
        default=None,
        max_length=150,
        examples=["vecino.p20@gmail.com"],
        description="Correo electrónico del propietario para el onboarding masivo",
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
    owner_email: str | None = None
    initial_password: str = Field(
        ...,
        description="Contraseña inicial autogenerada para enviar por Email/WhatsApp",
    )
    login_url: str = Field(
        ...,
        examples=["https://vc3.lasvillas.club"],
        description="URL del portal correspondiente a la Villa del vecino",
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
    limit: int = Field(..., ge=1, le=100, description="Cantidad máxima de lotes por página")
    total_pages: int = Field(..., ge=0, description="Cantidad total de páginas disponibles")
    has_next: bool = Field(..., description="True si existen más páginas después de la actual")
