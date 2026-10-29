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
class GuestPass(BaseModel):
    villa_id: str = Field(default="VC3", examples=["VC1", "VC2", "VC3"])
    code: str
    lot_id: str = Field(..., examples=["B14", "U28", "P20"])
    pass_type: PassType
    visitor_info: str = Field(
        ..., examples=["Rappi", "Taxi", "Visitante: Mitchell Salgado"]
    )
    status: PassStatus = PassStatus.ACTIVE
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    checked_in_dni: str | None = Field(
        default=None,
        description="DNI o documento registrado por el vigilante al momento del ingreso",
    )
    checked_in_plate: str | None = Field(
        default=None,
        description="Placa del vehículo registrada por el vigilante en garita",
    )


# --- Contratos HTTP (Request / Response) ---
class CreatePassRequest(BaseModel):
    """
    El residente autenticado ya NO envía villa_id ni lot_id (se toman de su Token JWT).
    Solo elige pass_type y opcionalmente una nota breve en visitor_info.
    """

    pass_type: PassType
    visitor_info: str | None = Field(
        default=None,
        max_length=100,
        examples=["Rappi", "Amazon", "Visitante: Mitchell Salgado"],
        description="Opcional: detalle breve del delivery, taxi o nombre del invitado",
    )


class VerifyPassRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        examples=["ABC987"],
        description="Código de 6 caracteres (o leído del QR) presentado en garita",
    )
    visitor_dni: str = Field(
        ...,
        min_length=8,
        max_length=12,
        examples=["45891230"],
        description="DNI (8 dígitos) o Carnet de Extranjería verificado por el vigilante",
    )
    vehicle_plate: str | None = Field(
        default=None,
        max_length=15,
        examples=["B7J-412"],
        description="Placa del vehículo/moto (Obligatoria para TAXI y DELIVERY; opcional para GUEST a pie)",
    )


class GatehouseVerificationResponse(BaseModel):
    authorized: bool
    message: str
    villa_id: str | None = None
    lot_id: str | None = None
    owner_name: str | None = None
    pass_type: PassType | None = None
    visitor_info: str | None = None
    checked_in_dni: str | None = None
    checked_in_plate: str | None = None
