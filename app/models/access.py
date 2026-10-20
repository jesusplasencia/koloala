from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PassType(StrEnum):
    DELIVERY = "DELIVERY"
    TAXI = "TAXI"
    GUEST = "GUEST"


class PassStatus(StrEnum):
    ACTIVE = "ACTIVE"
    USED = "USED"
    EXPIRED = "EXPIRED"


# --- Entidades de Dominio ---
class PropertyLot(BaseModel):
    lot_id: str = Field(
        ..., examples=["MZ-P-L20"], description="Identificador Manzana y Lote"
    )
    owner_name: str
    is_maintenance_up_to_date: bool


class GuestPass(BaseModel):
    code: str
    lot_id: str
    pass_type: PassType
    visitor_info: str = Field(
        ..., examples=["Rappi - Moto", "Taxi XYZ123", "Visitante: Mitchell Salgado"]
    )
    status: PassStatus = PassStatus.ACTIVE
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


# --- Contratos HTTP (Request / Response) ---
class CreatePassRequest(BaseModel):
    lot_id: str = Field(
        ..., examples=["MZ-P-L20"], description="Identificador Manzana y Lote"
    )
    pass_type: PassType
    visitor_info: str = Field(
        ...,
        min_length=3,
        max_length=100,
        examples=["Rappi - Moto", "Taxi XYZ123", "Visitante: Mitchell Salgado"],
    )


class VerifyPassRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        examples=["ABC987"],
        description="Código del pase a verificar",
    )


class GatehouseVerificationResponse(BaseModel):
    authorized: bool
    message: str
    lot_id: str | None = None
    owner_name: str | None = None
    pass_type: PassType | None = None
    visitor_info: str | None = None
