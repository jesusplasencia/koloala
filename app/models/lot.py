from datetime import datetime

from pydantic import BaseModel, Field


# --- Entidades de Dominio ---
class PropertyLot(BaseModel):
    lot_id: str = Field(
        ..., examples=["MZ-P-L20"], description="Identificador Manzana y Lote"
    )
    owner_name: str
    is_maintenance_up_to_date: bool
    created_at: datetime
    updated_at: datetime


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
        min_length=3,
        max_length=20,
        examples=["MZ-B-L14"],
        description="Identificador Manzana y Lote",
    )
    owner_name: str = Field(
        ..., min_length=3, max_length=100, examples=["Jesus Plasencia"]
    )
    is_maintenance_up_to_date: bool


class BulkUpsertLotsRequest(BaseModel):
    lots: list[BulkLotItem] = Field(
        ...,
        min_length=1,
        description="Lista de lotes a sincronizar (crea nuevos o actualiza existentes)",
    )


class BulkUpsertLotsResponse(BaseModel):
    processed_count: int
    created_count: int
    updated_count: int
    lots: list[PropertyLot]
