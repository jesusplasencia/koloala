"""Ingreso del propio residente (ANCHOR / HOUSEHOLD) con su Carnet QR rotativo.

El carnet es de la PERSONA (celular), no del lote: si es ANCHOR de Q03 y HOUSEHOLD de P20,
el guardia ve ambas membresías. El carnet no se gasta: cada ingreso es un evento.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class EntryMethod(StrEnum):
    QR = "QR"
    MANUAL = "MANUAL"  # Llegó sin celular: el guardia lo registra por lote + celular dictado


class EntryLot(BaseModel):
    lot_id: str
    membership_role: str
    maintenance_ok: bool


class EntryEvent(BaseModel):
    villa_id: str
    at: datetime
    person_phone: str
    person_name: str
    lots: list[EntryLot]
    method: EntryMethod
    guard: str
    vehicle_plate: str | None = None
    plate_hmac: str | None = None


class CarnetResponse(BaseModel):
    payload: str = Field(..., description="Contenido del QR; rota cada `window_seconds`")
    expires_in: int
    window_seconds: int
    full_name: str
    lots: list[EntryLot]


class ResidentEntryRequest(BaseModel):
    qr_payload: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(
        default=None, max_length=20, description="Solo ingreso MANUAL: celular que dicta el residente"
    )
    lot_id: str | None = Field(
        default=None, max_length=15, description="Solo ingreso MANUAL: lote que dice el residente"
    )
    vehicle_plate: str | None = Field(default=None, max_length=15)

    @model_validator(mode="after")
    def _one_method(self) -> "ResidentEntryRequest":
        if not self.qr_payload and not (self.phone and self.lot_id):
            raise ValueError("Envía qr_payload, o phone + lot_id para ingreso manual.")
        return self


class ResidentEntryView(BaseModel):
    authorized: bool
    message: str
    person_name: str
    lots: list[EntryLot]
    warnings: list[str] = Field(default_factory=list)
    method: EntryMethod
    at: datetime
    vehicle_plate: str | None = None


class MyEntriesResponse(BaseModel):
    villa_id: str
    items: list[ResidentEntryView]
