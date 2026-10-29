import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def normalize_lot_id(raw_lot_id: str) -> str:
    """
    Normaliza el código de lote al estándar corto de Villa Club 3: 'B14', 'U28', 'P20', 'F08'.
    """
    cleaned = raw_lot_id.strip().upper()
    match = re.fullmatch(r"([A-Z])\s*-?\s*(\d{1,2})", cleaned)
    if match:
        letter, number = match.groups()
        return f"{letter}{int(number):02d}"
    return cleaned


# --- Entidad de Dominio: Lote Físico ---
class PropertyLot(BaseModel):
    lot_id: str = Field(
        ...,
        examples=["B14", "U28", "P20"],
        description="Identificador corto Manzana y Lote (ej. B14, U28, P20)",
    )
    owner_name: str
    is_maintenance_up_to_date: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("lot_id", mode="before")
    @classmethod
    def _validate_lot_id(cls, value: str) -> str:
        return normalize_lot_id(value)


# --- Contratos HTTP (Request / Response) para el Administrador ---
class UpdateLotMaintenanceRequest(BaseModel):
    is_maintenance_up_to_date: bool = Field(
        ..., description="True si pagó su cuota del mes, False si entró en mora"
    )
    owner_name: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        description="Opcional: actualizar el nombre del propietario si el lote se vendió",
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
        ..., min_length=3, max_length=100, examples=["Jesus Plasencia"]
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
        description="Lista de lotes a sincronizar (crea nuevos o actualiza existentes)",
    )


class GeneratedLotCredential(BaseModel):
    lot_id: str
    username: str
    initial_password: str = Field(
        ...,
        description="Contraseña por defecto autogenerada para enviar por WhatsApp/Email al propietario",
    )


class BulkUpsertLotsResponse(BaseModel):
    processed_count: int
    created_count: int
    updated_count: int
    lots: list[PropertyLot]
    generated_credentials: list[GeneratedLotCredential] = Field(
        default_factory=list,
        description="Credenciales iniciales generadas únicamente para los lotes nuevos creados en este lote",
    )
