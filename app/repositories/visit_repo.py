from datetime import UTC, datetime
from typing import Any, Protocol

from app.models.lot import (
    LotMembership,
    LotType,
    PropertyLot,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.models.entry import EntryEvent
from app.models.visit import CatalogBulkItem, VisitPass
from app.repositories.lots_repo import in_memory_lot_repo


class VisitRepositoryProtocol(Protocol):
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None: ...
    async def list_catalog(self, villa_id: str) -> list[PropertyLot]: ...
    async def upsert_catalog_item(
        self, villa_id: str, item: CatalogBulkItem
    ) -> tuple[PropertyLot, bool]: ...
    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None: ...
    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]: ...
    async def save_visit(self, visit: VisitPass) -> VisitPass: ...
    async def get_visit(self, villa_id: str, token: str) -> VisitPass | None: ...
    async def get_visit_by_code(self, villa_id: str, short_code: str) -> VisitPass | None: ...
    async def search_visits(
        self,
        villa_id: str,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        doc_hmac: str | None = None,
        plate_hmac: str | None = None,
        destination_lot_id: str | None = None,
        only_checked_in: bool = False,
        limit: int = 200,
    ) -> list[VisitPass]: ...
    async def shadow_pii_before(self, cutoff: datetime) -> int: ...
    async def log_data_access(self, entry: dict[str, Any]) -> None: ...
    async def save_entry_event(self, event: EntryEvent) -> EntryEvent: ...
    async def list_entry_events(
        self,
        villa_id: str,
        *,
        person_phone: str | None = None,
        lot_id: str | None = None,
        plate_hmac: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 200,
    ) -> list[EntryEvent]: ...


def _matches(
    v: VisitPass,
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    doc_hmac: str | None,
    plate_hmac: str | None,
    destination_lot_id: str | None,
    only_checked_in: bool,
) -> bool:
    if only_checked_in and v.checked_in_at is None:
        return False
    if date_from and v.created_at < date_from:
        return False
    if date_to and v.created_at > date_to:
        return False
    if destination_lot_id and v.destination_lot_id != destination_lot_id:
        return False
    if plate_hmac and v.plate_hmac != plate_hmac:
        return False
    if doc_hmac and not any(a.doc_hmac == doc_hmac for a in v.attendees):
        return False
    return True


class InMemoryVisitRepository:
    def __init__(
        self,
        lots_store: dict[tuple[str, str], PropertyLot] | None = None,
        memberships_store: dict[tuple[str, str, str], LotMembership] | None = None,
    ) -> None:
        self._lots = lots_store if lots_store is not None else in_memory_lot_repo.lots_store
        self._memberships = (
            memberships_store
            if memberships_store is not None
            else in_memory_lot_repo.memberships_store
        )
        self._visits: dict[tuple[str, str], VisitPass] = {}
        self.data_access_log: list[dict[str, Any]] = []
        self.entry_events: list[EntryEvent] = []

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        return self._lots.get((normalize_villa_id(villa_id), normalize_lot_id(lot_id)))

    async def list_catalog(self, villa_id: str) -> list[PropertyLot]:
        norm = normalize_villa_id(villa_id)
        items = [
            lot
            for lot in self._lots.values()
            if lot.villa_id == norm and lot.is_public and lot.lot_type != LotType.LOT
        ]
        return sorted(items, key=lambda lot: (lot.lot_type, lot.lot_id))

    async def upsert_catalog_item(
        self, villa_id: str, item: CatalogBulkItem
    ) -> tuple[PropertyLot, bool]:
        norm = normalize_villa_id(villa_id)
        key = (norm, normalize_lot_id(item.lot_id))
        now = datetime.now(UTC)
        existing = self._lots.get(key)
        if existing:
            updated = existing.model_copy(
                update={
                    "lot_type": item.lot_type,
                    "display_name": item.display_name,
                    "is_public": item.is_public,
                    "updated_at": now,
                }
            )
            self._lots[key] = updated
            return updated, False
        lot = PropertyLot(
            villa_id=norm,
            lot_id=key[1],
            owner_name=item.display_name,
            is_maintenance_up_to_date=True,
            lot_type=item.lot_type,
            display_name=item.display_name,
            is_public=item.is_public,
            created_at=now,
            updated_at=now,
        )
        self._lots[key] = lot
        return lot, True

    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        key = (
            normalize_villa_id(villa_id),
            normalize_lot_id(lot_id),
            normalize_phone_username(phone) or phone.strip(),
        )
        mem = self._memberships.get(key)
        return mem if mem and mem.is_active else None

    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        return [
            m
            for m in self._memberships.values()
            if m.villa_id == norm_villa and m.phone == norm_phone and m.is_active
        ]

    async def save_visit(self, visit: VisitPass) -> VisitPass:
        self._visits[(visit.villa_id, visit.token)] = visit.model_copy(deep=True)
        return visit

    async def get_visit(self, villa_id: str, token: str) -> VisitPass | None:
        v = self._visits.get((normalize_villa_id(villa_id), token))
        return v.model_copy(deep=True) if v else None

    async def get_visit_by_code(self, villa_id: str, short_code: str) -> VisitPass | None:
        norm = normalize_villa_id(villa_id)
        code = short_code.strip().upper()
        for v in self._visits.values():
            if v.villa_id == norm and v.short_code == code:
                return v.model_copy(deep=True)
        return None

    async def search_visits(
        self,
        villa_id: str,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        doc_hmac: str | None = None,
        plate_hmac: str | None = None,
        destination_lot_id: str | None = None,
        only_checked_in: bool = False,
        limit: int = 200,
    ) -> list[VisitPass]:
        norm = normalize_villa_id(villa_id)
        items = [
            v.model_copy(deep=True)
            for v in self._visits.values()
            if v.villa_id == norm
            and _matches(
                v,
                date_from=date_from,
                date_to=date_to,
                doc_hmac=doc_hmac,
                plate_hmac=plate_hmac,
                destination_lot_id=destination_lot_id,
                only_checked_in=only_checked_in,
            )
        ]
        items.sort(key=lambda v: v.created_at, reverse=True)
        return items[:limit]

    async def shadow_pii_before(self, cutoff: datetime) -> int:
        now = datetime.now(UTC)
        count = 0
        for v in self._visits.values():
            if v.created_at >= cutoff or v.shadowed_at is not None:
                continue
            v.vehicle_plate = None
            v.shadowed_at = now
            for a in v.attendees:
                a.doc_number = None
                a.shadowed_at = now
            count += 1
        return count

    async def log_data_access(self, entry: dict[str, Any]) -> None:
        self.data_access_log.append(entry)

    async def save_entry_event(self, event: EntryEvent) -> EntryEvent:
        self.entry_events.append(event.model_copy(deep=True))
        return event

    async def list_entry_events(
        self,
        villa_id: str,
        *,
        person_phone: str | None = None,
        lot_id: str | None = None,
        plate_hmac: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 200,
    ) -> list[EntryEvent]:
        norm = normalize_villa_id(villa_id)
        items = [
            e
            for e in self.entry_events
            if e.villa_id == norm
            and (not person_phone or e.person_phone == person_phone)
            and (not lot_id or any(lot.lot_id == lot_id for lot in e.lots))
            and (not plate_hmac or e.plate_hmac == plate_hmac)
            and (not date_from or e.at >= date_from)
            and (not date_to or e.at <= date_to)
        ]
        items.sort(key=lambda e: e.at, reverse=True)
        return [e.model_copy(deep=True) for e in items[:limit]]


in_memory_visit_repo = InMemoryVisitRepository()
