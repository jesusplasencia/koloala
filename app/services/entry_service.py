import hashlib
import hmac
import time
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.config import settings
from app.core.pii import normalize_plate, pii_hmac
from app.core.security import AuthenticatedIdentity
from app.models.entry import (
    CarnetResponse,
    EntryEvent,
    EntryLot,
    EntryMethod,
    MyEntriesResponse,
    ResidentEntryRequest,
    ResidentEntryView,
)
from app.models.lot import normalize_lot_id, normalize_phone_username, normalize_villa_id
from app.repositories.visit_repo import VisitRepositoryProtocol

_PREFIX = "R1"


def _sign(villa: str, phone: str, window: int) -> str:
    msg = f"{villa}|{phone}|{window}".encode()
    return hmac.new(settings.resident_qr_secret.encode(), msg, hashlib.sha256).hexdigest()[:16]


def build_carnet_payload(villa: str, phone: str, now_s: float | None = None) -> tuple[str, int]:
    w = settings.resident_qr_window_seconds
    t = time.time() if now_s is None else now_s
    window = int(t // w)
    expires_in = w - int(t % w)
    return f"{_PREFIX}.{villa}.{phone}.{window}.{_sign(villa, phone, window)}", expires_in


def parse_carnet_payload(payload: str, now_s: float | None = None) -> tuple[str, str] | None:
    """Devuelve (villa, phone) si la firma es válida y la ventana es la actual ±1."""
    parts = payload.strip().split(".")
    if len(parts) != 5 or parts[0] != _PREFIX or not parts[3].isdigit():
        return None
    _, villa, phone, window_s, sig = parts
    window = int(window_s)
    t = time.time() if now_s is None else now_s
    current = int(t // settings.resident_qr_window_seconds)
    if abs(current - window) > 1:
        return None
    if not hmac.compare_digest(sig, _sign(villa, phone, window)):
        return None
    return villa, phone


class EntryService:
    def __init__(self, repo: VisitRepositoryProtocol) -> None:
        self.repo = repo

    async def _active_lots(self, villa: str, phone: str) -> tuple[str | None, list[EntryLot]]:
        mems = await self.repo.list_memberships_by_phone(villa, phone)
        lots: list[EntryLot] = []
        name: str | None = None
        for m in sorted(mems, key=lambda m: (str(m.membership_role), m.lot_id)):
            lot = await self.repo.get_lot(villa, m.lot_id)
            lots.append(
                EntryLot(
                    lot_id=m.lot_id,
                    membership_role=str(m.membership_role),
                    maintenance_ok=bool(lot and lot.is_maintenance_up_to_date),
                )
            )
            name = name or m.full_name
        return name, lots

    async def carnet(self, identity: AuthenticatedIdentity) -> CarnetResponse:
        villa = normalize_villa_id(identity.villa_id)
        phone = normalize_phone_username(identity.username) or identity.username
        name, lots = await self._active_lots(villa, phone)
        if not lots:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"No tienes membresía activa (ANCHOR ni HOUSEHOLD) en {villa}; no hay carnet.",
            )
        payload, expires_in = build_carnet_payload(villa, phone)
        return CarnetResponse(
            payload=payload,
            expires_in=expires_in,
            window_seconds=settings.resident_qr_window_seconds,
            full_name=name or identity.full_name or phone,
            lots=lots,
        )

    async def register_entry(
        self, guard: AuthenticatedIdentity, payload: ResidentEntryRequest
    ) -> ResidentEntryView:
        villa = normalize_villa_id(guard.villa_id)
        if payload.qr_payload:
            parsed = parse_carnet_payload(payload.qr_payload)
            if not parsed:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "CARNET NO VÁLIDO o vencido: pide al residente que abra su carnet de nuevo.",
                )
            qr_villa, phone = parsed
            if qr_villa != villa:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, f"Este carnet es de {qr_villa}, no de {villa}."
                )
            method = EntryMethod.QR
        else:
            phone = normalize_phone_username(payload.phone) or ""
            method = EntryMethod.MANUAL

        name, lots = await self._active_lots(villa, phone)
        if method == EntryMethod.MANUAL:
            wanted = normalize_lot_id(payload.lot_id or "")
            if not any(lot.lot_id == wanted for lot in lots):
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "No hay un residente activo con ese celular en ese lote. Regístralo como visita.",
                )
        if not lots:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "La persona ya no tiene membresía activa en la Villa. Regístrala como visita.",
            )
        warnings = [f"Lote {lot.lot_id} con deuda de mantenimiento" for lot in lots if not lot.maintenance_ok]
        plate = normalize_plate(payload.vehicle_plate)
        now = datetime.now(UTC)
        event = EntryEvent(
            villa_id=villa,
            at=now,
            person_phone=phone,
            person_name=name or phone,
            lots=lots,
            method=method,
            guard=guard.username,
            vehicle_plate=plate,
            plate_hmac=pii_hmac(plate),
        )
        await self.repo.save_entry_event(event)
        log_audit_event(
            event="RESIDENT_ENTRY",
            actor=f"{villa}:{guard.username}",
            client_ip="internal",
            user_agent="gate",
            details={"villa_id": villa, "lots": [lot.lot_id for lot in lots], "method": str(method)},
        )
        return self._view(event, warnings)

    def _view(self, e: EntryEvent, warnings: list[str] | None = None) -> ResidentEntryView:
        lots = ", ".join(lot.lot_id for lot in e.lots)
        return ResidentEntryView(
            authorized=True,
            message=f"RESIDENTE: {e.person_name} - {lots}",
            person_name=e.person_name,
            lots=e.lots,
            warnings=warnings or [],
            method=e.method,
            at=e.at,
            vehicle_plate=e.vehicle_plate,
        )

    async def my_entries(self, identity: AuthenticatedIdentity, limit: int = 100) -> MyEntriesResponse:
        villa = normalize_villa_id(identity.villa_id)
        phone = normalize_phone_username(identity.username) or identity.username
        events = await self.repo.list_entry_events(villa, person_phone=phone, limit=limit)
        return MyEntriesResponse(villa_id=villa, items=[self._view(e) for e in events])

    async def search(
        self,
        villa_id: str,
        *,
        lot_id: str | None,
        plate: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int = 200,
    ) -> list[ResidentEntryView]:
        events = await self.repo.list_entry_events(
            villa_id,
            lot_id=normalize_lot_id(lot_id) if lot_id else None,
            plate_hmac=pii_hmac(normalize_plate(plate)),
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return [self._view(e) for e in events]
