"""Pases grupales sin cuenta: el QR es la única puerta de entrada.

- HOST_INVITE: un ANCHOR/HOUSEHOLD invita a su lote (familia de Canadá a la P20). No se piden celulares.
- GUARD_DESK: quien llega sin invitación muestra su DNI físico; el vigilante fotografía el
  reverso (OCR en su teléfono, la foto NO se guarda) y registra el ingreso a un servicio del catálogo.
  No se genera QR para el visitante: queda un registro formal.

Nadie se queda afuera por no traer DNI físico: se registra cómo se verificó su identidad.
"""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.pii import normalize_doc
from app.models.entry import ResidentEntryView
from app.models.lot import LotType


class VisitOrigin(StrEnum):
    HOST_INVITE = "HOST_INVITE"
    GUARD_DESK = "GUARD_DESK"


class VisitKind(StrEnum):
    GUEST = "GUEST"  # Visita (familia, amigos)
    TAXI = "TAXI"  # Placa obligatoria, vigencia corta
    DELIVERY = "DELIVERY"  # Placa obligatoria, vigencia corta


class VisitStatus(StrEnum):
    ACTIVE = "ACTIVE"
    USED = "USED"
    EXPIRED = "EXPIRED"


class DocType(StrEnum):
    DNI = "DNI"
    CE = "CE"
    PASAPORTE = "PASAPORTE"
    NINGUNO = "NINGUNO"


class DocSource(StrEnum):
    OCR = "OCR"  # Leído de la foto del reverso del DNI (MRZ) en el teléfono del vigilante
    MANUAL = "MANUAL"  # Escrito a mano


class Verification(StrEnum):
    DOC_SHOWN = "DOC_SHOWN"  # Mostró su DNI físico
    DOC_DIGITAL = "DOC_DIGITAL"  # Mostró su DNI electrónico / foto en el celular
    DECLARED = "DECLARED"  # Lo dijo de palabra (o no tiene)
    VOUCHED = "VOUCHED"  # Un acompañante con documento responde por la persona


_DOC_PATTERNS = {
    DocType.DNI: r"\d{8}",
    DocType.CE: r"[A-Z0-9]{9,12}",
    DocType.PASAPORTE: r"[A-Z0-9]{6,12}",
}


# --- Entidades de dominio ---
class Attendee(BaseModel):
    attendee_no: int
    first_names: str
    last_names: str | None = None
    doc_type: DocType = DocType.NINGUNO
    doc_number: str | None = None
    doc_hmac: str | None = None
    doc_last2: str | None = None
    doc_source: DocSource | None = None
    verification: Verification | None = None
    vouched_by_no: int | None = None
    checked_in_at: datetime | None = None
    checked_in_by_guard: str | None = None
    created_at: datetime
    shadowed_at: datetime | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_names} {self.last_names or ''}".strip()


class VisitPass(BaseModel):
    villa_id: str
    token: str
    short_code: str
    origin: VisitOrigin
    kind: VisitKind = VisitKind.GUEST
    destination_lot_id: str
    destination_type: LotType
    destination_label: str
    host_phone: str | None = None
    host_name: str | None = None
    host_role: str | None = None
    party_size_expected: int = 1
    note: str | None = None
    vehicle_plate: str | None = None
    plate_hmac: str | None = None
    status: VisitStatus = VisitStatus.ACTIVE
    created_at: datetime
    valid_from: datetime
    valid_until: datetime
    checked_in_at: datetime | None = None
    checked_in_by_guard: str | None = None
    shadowed_at: datetime | None = None
    attendees: list[Attendee] = Field(default_factory=list)


# --- Contratos HTTP ---
class AttendeeInput(BaseModel):
    first_names: str = Field(..., min_length=1, max_length=80, examples=["JESUS MANUEL"])
    last_names: str | None = Field(default=None, max_length=80, examples=["PLASENCIA"])
    doc_type: DocType | None = Field(
        default=None, description="Si se omite: DNI cuando hay número, NINGUNO cuando no"
    )
    doc_number: str | None = Field(default=None, max_length=20, examples=["60998877"])
    doc_source: DocSource | None = Field(
        default=None, description="OCR si se leyó de la foto del DNI; MANUAL si se escribió"
    )

    @field_validator("first_names", "last_names", mode="before")
    @classmethod
    def _clean_names(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = " ".join(str(v).split()).upper()
        return cleaned or None

    @model_validator(mode="after")
    def _validate_doc(self) -> "AttendeeInput":
        number = normalize_doc(self.doc_number)
        doc_type = self.doc_type or (DocType.DNI if number else DocType.NINGUNO)
        if doc_type == DocType.NINGUNO:
            number = None
        elif not number:
            raise ValueError(f"Falta el número de {doc_type}.")
        elif not re.fullmatch(_DOC_PATTERNS[doc_type], number):
            raise ValueError(f"Número de {doc_type} inválido.")
        self.doc_type = doc_type
        self.doc_number = number
        return self


class CreateInviteRequest(BaseModel):
    kind: VisitKind = Field(
        default=VisitKind.GUEST,
        description="GUEST (visita), TAXI (30 min) o DELIVERY (45 min); TAXI/DELIVERY exigen placa en garita",
    )
    lot_id: str | None = Field(default=None, max_length=15, examples=["P20"])
    party_size_expected: int = Field(default=1, ge=1, le=20)
    valid_from: datetime | None = None
    valid_hours: int = Field(default=24, ge=1, le=72)
    note: str | None = Field(default=None, max_length=120, examples=["Familia de Canadá"])
    attendees: list[AttendeeInput] = Field(
        default_factory=list,
        max_length=20,
        description="Opcional: nombres precargados; si no, se completan en el enlace o en garita",
    )


class AddAttendeesRequest(BaseModel):
    attendees: list[AttendeeInput] = Field(..., min_length=1, max_length=20)


class AttendeeCheckIn(BaseModel):
    attendee_no: int | None = Field(
        default=None, description="Asistente ya listado en el pase; vacío = persona nueva"
    )
    person: AttendeeInput | None = Field(
        default=None, description="Datos del asistente nuevo o corrección de datos"
    )
    verification: Verification | None = Field(
        default=None,
        description="Si se omite: DOC_SHOWN con documento, DECLARED sin documento",
    )
    vouched_by_no: int | None = Field(
        default=None, description="attendee_no del acompañante que responde por esta persona"
    )

    @model_validator(mode="after")
    def _require_ref(self) -> "AttendeeCheckIn":
        if self.attendee_no is None and self.person is None:
            raise ValueError("Indica attendee_no o los datos de la persona.")
        return self


class CheckInRequest(BaseModel):
    attendees: list[AttendeeCheckIn] = Field(..., min_length=1, max_length=20)
    vehicle_plate: str | None = Field(default=None, max_length=15)


class GuardDeskVisitRequest(BaseModel):
    destination_lot_id: str = Field(..., max_length=15)
    attendees: list[AttendeeCheckIn] = Field(..., min_length=1, max_length=20)
    vehicle_plate: str | None = Field(default=None, max_length=15)


class CatalogItem(BaseModel):
    lot_id: str
    lot_type: LotType
    display_name: str


class PublicVillaResponse(BaseModel):
    villa_id: str
    catalog: list[CatalogItem]


class CatalogBulkItem(BaseModel):
    lot_id: str = Field(..., min_length=2, max_length=10, examples=["A01"])
    display_name: str = Field(..., min_length=2, max_length=100, examples=["A01 - Panadería"])
    lot_type: LotType = Field(default=LotType.MINI_LOT)
    is_public: bool = True


class CatalogBulkRequest(BaseModel):
    items: list[CatalogBulkItem] = Field(..., min_length=1, max_length=500)


class CatalogBulkResponse(BaseModel):
    villa_id: str
    created_count: int
    updated_count: int
    items: list[CatalogItem]


class InviteResponse(BaseModel):
    token: str
    short_code: str
    kind: VisitKind = VisitKind.GUEST
    villa_id: str
    destination_label: str
    party_size_expected: int
    valid_from: datetime
    valid_until: datetime
    share_path: str = Field(..., description="Ruta del enlace para compartir por WhatsApp")


class PublicPassView(BaseModel):
    """Vista del pase para quien tiene el enlace: nunca incluye DNIs ni teléfonos."""

    short_code: str
    villa_id: str
    destination_label: str
    status: VisitStatus
    valid_from: datetime
    valid_until: datetime
    party_size_expected: int
    attendee_names: list[str]


class GuardAttendeeView(BaseModel):
    attendee_no: int
    full_name: str
    doc_type: DocType
    doc_display: str | None
    doc_source: DocSource | None = None
    verification: Verification | None
    vouched_by_no: int | None
    checked_in_at: datetime | None


class GuardPassView(BaseModel):
    authorized: bool
    message: str
    short_code: str
    origin: VisitOrigin
    kind: VisitKind
    destination_label: str
    host_name: str | None
    host_role: str | None
    party_size_expected: int
    status: VisitStatus
    created_at: datetime
    valid_until: datetime
    vehicle_plate: str | None
    checked_in_at: datetime | None
    checked_in_by_guard: str | None
    attendees: list[GuardAttendeeView]


class VisitLogResponse(BaseModel):
    villa_id: str
    total_passes: int
    checked_in_passes: int
    total_people_in: int
    resident_entries: int = 0
    docs_revealed: bool = False
    items: list[GuardPassView]
    resident_items: list[ResidentEntryView] = Field(default_factory=list)


class AccessSearchResponse(BaseModel):
    villa_id: str
    result_count: int
    items: list[GuardPassView]
    resident_entries: list[ResidentEntryView] = Field(default_factory=list)
