from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PassType(StrEnum):
    DELIVERY = "DELIVERY"
    TAXI = "TAXI"
    GUEST = "GUEST"
    RESIDENT_QR = "RESIDENT_QR"
    COMMERCE = "COMMERCE"


class PassStatus(StrEnum):
    ACTIVE = "ACTIVE"
    USED = "USED"
    EXPIRED = "EXPIRED"


# --- Entidades de Dominio ---
class GuestPass(BaseModel):
    villa_id: str = Field(default="VC3", examples=["VC1", "VC2", "VC3"])
    code: str
    lot_id: str = Field(
        ...,
        examples=["P20", "B14", "PANADERIA", "BOTICA"],
        description="Código de lote físico (para residentes/invitados) o destino comercial/servicio",
    )
    pass_type: PassType
    visitor_info: str = Field(
        ...,
        examples=[
            "Rappi",
            "Taxi",
            "Amigo Caillou",
            "Ingreso Personal Residente",
            "Panadería",
        ],
    )
    destination: str = Field(
        default="Lote",
        examples=["Lote P20", "Panadería", "Botica", "Parque", "Canchita"],
        description="Destino dentro de la Villa",
    )
    created_by_phone: str | None = Field(
        default=None,
        examples=["980180219", "916220518"],
        description="Celular/usuario de quien generó el QR",
    )
    created_by_name: str | None = Field(
        default=None,
        examples=["Angel Plasencia", "Jesús Plasencia"],
        description="Nombre de quien generó el QR para trazabilidad forense",
    )
    created_by_role: str | None = Field(
        default=None,
        examples=["ANCHOR", "HOUSEHOLD", "VISITOR"],
        description="Rol con el que generó el QR (ANCHOR, HOUSEHOLD o VISITOR)",
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
    checked_in_by_guard: str | None = Field(
        default=None,
        description="Usuario del vigilante o administrador que validó el ingreso en garita",
    )


# --- Contratos HTTP (Request / Response) ---
class CreatePassRequest(BaseModel):
    """
    Permite a un ANCHOR, HOUSEHOLD o VISITOR generar su código/QR:
    - Si tiene múltiples lotes en la Villa (ej. P20 y Q02), puede especificar `lot_id`.
    - Si es un VISITOR que va a un servicio interno (COMMERCE), puede indicar `destination`
      (ej. 'PANADERIA', 'BOTICA', 'PARQUE', 'CANCHITA').
    """

    pass_type: PassType
    visitor_info: str | None = Field(
        default=None,
        max_length=100,
        examples=["Rappi", "Amigo Caillou", "Compra en Panadería"],
        description="Detalle breve del delivery, taxi, nombre del invitado o motivo de visita",
    )
    lot_id: str | None = Field(
        default=None,
        max_length=20,
        examples=["P20", "Q02"],
        description="Opcional: si el residente es ANCHOR/HOUSEHOLD en varios lotes, elige para cuál lote genera el QR",
    )
    destination: str | None = Field(
        default=None,
        max_length=60,
        examples=["PANADERIA", "BOTICA", "PARQUE", "CANCHITA"],
        description="Opcional: servicio o comercio interno al que se dirige un VISITOR",
    )


class VerifyPassRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        examples=["ABC987"],
        description="Código de 6 caracteres (o leído del QR) presentado en garita",
    )
    visitor_dni: str | None = Field(
        default=None,
        min_length=8,
        max_length=12,
        examples=["45891230"],
        description="DNI (8 dígitos) o Carnet de Extranjería verificado por el vigilante (si ya viene del QR de visitante/residente, se autocompleta)",
    )
    vehicle_plate: str | None = Field(
        default=None,
        max_length=15,
        examples=["B7J-412"],
        description="Placa del vehículo/moto (Obligatoria para TAXI y DELIVERY; opcional para peatones)",
    )


class GatehouseVerificationResponse(BaseModel):
    authorized: bool
    message: str
    villa_id: str | None = None
    lot_id: str | None = None
    destination: str | None = None
    owner_name: str | None = None
    pass_type: PassType | None = None
    visitor_info: str | None = None
    created_by_phone: str | None = None
    created_by_name: str | None = None
    created_by_role: str | None = None
    checked_in_dni: str | None = None
    checked_in_plate: str | None = None
    checked_in_by_guard: str | None = None


class DailyLogResponse(BaseModel):
    villa_id: str
    total_passes: int
    checked_in_count: int
    active_pending_count: int
    items: list[GuestPass]
