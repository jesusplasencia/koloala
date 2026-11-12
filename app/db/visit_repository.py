from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DataAccessLogORM,
    EntryEventORM,
    PassAttendeeORM,
    PropertyLotORM,
    VisitPassORM,
)
from app.db.repositories import PostgresMembershipLookup
from app.models.entry import EntryEvent, EntryLot, EntryMethod
from app.models.lot import LotType, PropertyLot, normalize_lot_id, normalize_villa_id
from app.models.visit import (
    Attendee,
    CatalogBulkItem,
    DocType,
    Verification,
    VisitKind,
    VisitOrigin,
    VisitPass,
    VisitStatus,
)

_PASS_FIELDS = (
    "short_code",
    "origin",
    "kind",
    "destination_lot_id",
    "destination_type",
    "destination_label",
    "host_phone",
    "host_name",
    "host_role",
    "party_size_expected",
    "note",
    "vehicle_plate",
    "plate_hmac",
    "status",
    "created_at",
    "valid_from",
    "valid_until",
    "checked_in_at",
    "checked_in_by_guard",
    "shadowed_at",
)
_ATTENDEE_FIELDS = (
    "first_names",
    "last_names",
    "doc_type",
    "doc_number",
    "doc_hmac",
    "doc_last2",
    "verification",
    "vouched_by_no",
    "checked_in_at",
    "checked_in_by_guard",
    "created_at",
    "shadowed_at",
)


def _plain(value: Any) -> Any:
    return str(value) if hasattr(value, "value") else value


class PostgresVisitRepository(PostgresMembershipLookup):
    """Reutiliza get_lot / get_membership / list_memberships_by_phone de PostgresMembershipLookup.
    Nunca emite DELETE (rol koloala_runtime solo tiene SELECT, INSERT, UPDATE)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_catalog(self, villa_id: str) -> list[PropertyLot]:
        stmt = (
            select(PropertyLotORM)
            .where(
                PropertyLotORM.villa_id == normalize_villa_id(villa_id),
                PropertyLotORM.is_public.is_(True),
                PropertyLotORM.lot_type != str(LotType.LOT),
            )
            .order_by(PropertyLotORM.lot_type, PropertyLotORM.lot_id)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._lot_to_domain(r) for r in rows]

    async def upsert_catalog_item(
        self, villa_id: str, item: CatalogBulkItem
    ) -> tuple[PropertyLot, bool]:
        key = (normalize_villa_id(villa_id), normalize_lot_id(item.lot_id))
        now = datetime.now(UTC)
        row = await self.session.get(PropertyLotORM, key)
        created = row is None
        if row is None:
            row = PropertyLotORM(
                villa_id=key[0],
                lot_id=key[1],
                owner_name=item.display_name,
                is_maintenance_up_to_date=True,
                created_at=now,
            )
            self.session.add(row)
        row.lot_type = str(item.lot_type)
        row.display_name = item.display_name
        row.is_public = item.is_public
        row.updated_at = now
        await self.session.flush()
        return self._lot_to_domain(row), created

    def _visit_to_domain(self, row: VisitPassORM, attendees: list[PassAttendeeORM]) -> VisitPass:
        return VisitPass(
            villa_id=row.villa_id,
            token=row.token,
            short_code=row.short_code,
            origin=VisitOrigin(row.origin),
            kind=VisitKind(row.kind or "GUEST"),
            destination_lot_id=row.destination_lot_id,
            destination_type=LotType(row.destination_type),
            destination_label=row.destination_label,
            host_phone=row.host_phone,
            host_name=row.host_name,
            host_role=row.host_role,
            party_size_expected=row.party_size_expected,
            note=row.note,
            vehicle_plate=row.vehicle_plate,
            plate_hmac=row.plate_hmac,
            status=VisitStatus(row.status),
            created_at=row.created_at,
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            checked_in_at=row.checked_in_at,
            checked_in_by_guard=row.checked_in_by_guard,
            shadowed_at=row.shadowed_at,
            attendees=[
                Attendee(
                    attendee_no=a.attendee_no,
                    first_names=a.first_names,
                    last_names=a.last_names,
                    doc_type=DocType(a.doc_type),
                    doc_number=a.doc_number,
                    doc_hmac=a.doc_hmac,
                    doc_last2=a.doc_last2,
                    verification=Verification(a.verification) if a.verification else None,
                    vouched_by_no=a.vouched_by_no,
                    checked_in_at=a.checked_in_at,
                    checked_in_by_guard=a.checked_in_by_guard,
                    created_at=a.created_at,
                    shadowed_at=a.shadowed_at,
                )
                for a in sorted(attendees, key=lambda x: x.attendee_no)
            ],
        )

    async def _load_attendees(self, villa_id: str, tokens: list[str]) -> dict[str, list[PassAttendeeORM]]:
        if not tokens:
            return {}
        stmt = select(PassAttendeeORM).where(
            PassAttendeeORM.villa_id == villa_id, PassAttendeeORM.token.in_(tokens)
        )
        grouped: dict[str, list[PassAttendeeORM]] = {t: [] for t in tokens}
        for a in (await self.session.execute(stmt)).scalars().all():
            grouped[a.token].append(a)
        return grouped

    async def save_visit(self, visit: VisitPass) -> VisitPass:
        key = (visit.villa_id, visit.token)
        row = await self.session.get(VisitPassORM, key)
        if row is None:
            row = VisitPassORM(villa_id=key[0], token=key[1])
            self.session.add(row)
        for f in _PASS_FIELDS:
            setattr(row, f, _plain(getattr(visit, f)))
        await self.session.flush()
        for a in visit.attendees:
            arow = await self.session.get(PassAttendeeORM, (key[0], key[1], a.attendee_no))
            if arow is None:
                arow = PassAttendeeORM(villa_id=key[0], token=key[1], attendee_no=a.attendee_no)
                self.session.add(arow)
            for f in _ATTENDEE_FIELDS:
                setattr(arow, f, _plain(getattr(a, f)))
        await self.session.flush()
        return visit

    async def get_visit(self, villa_id: str, token: str) -> VisitPass | None:
        norm = normalize_villa_id(villa_id)
        row = await self.session.get(VisitPassORM, (norm, token))
        if row is None:
            return None
        attendees = (await self._load_attendees(norm, [token]))[token]
        return self._visit_to_domain(row, attendees)

    async def get_visit_by_code(self, villa_id: str, short_code: str) -> VisitPass | None:
        norm = normalize_villa_id(villa_id)
        stmt = select(VisitPassORM).where(
            VisitPassORM.villa_id == norm,
            VisitPassORM.short_code == short_code.strip().upper(),
        )
        row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            return None
        attendees = (await self._load_attendees(norm, [row.token]))[row.token]
        return self._visit_to_domain(row, attendees)

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
        stmt = select(VisitPassORM).where(VisitPassORM.villa_id == norm)
        if date_from:
            stmt = stmt.where(VisitPassORM.created_at >= date_from)
        if date_to:
            stmt = stmt.where(VisitPassORM.created_at <= date_to)
        if destination_lot_id:
            stmt = stmt.where(VisitPassORM.destination_lot_id == destination_lot_id)
        if plate_hmac:
            stmt = stmt.where(VisitPassORM.plate_hmac == plate_hmac)
        if only_checked_in:
            stmt = stmt.where(VisitPassORM.checked_in_at.is_not(None))
        if doc_hmac:
            sub = select(PassAttendeeORM.token).where(
                PassAttendeeORM.villa_id == norm, PassAttendeeORM.doc_hmac == doc_hmac
            )
            stmt = stmt.where(VisitPassORM.token.in_(sub))
        stmt = stmt.order_by(VisitPassORM.created_at.desc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        grouped = await self._load_attendees(norm, [r.token for r in rows])
        return [self._visit_to_domain(r, grouped[r.token]) for r in rows]

    async def shadow_pii_before(self, cutoff: datetime) -> int:
        now = datetime.now(UTC)
        tokens_stmt = select(VisitPassORM.villa_id, VisitPassORM.token).where(
            VisitPassORM.created_at < cutoff, VisitPassORM.shadowed_at.is_(None)
        )
        targets = (await self.session.execute(tokens_stmt)).all()
        for villa_id, token in targets:
            await self.session.execute(
                update(PassAttendeeORM)
                .where(PassAttendeeORM.villa_id == villa_id, PassAttendeeORM.token == token)
                .values(doc_number=None, shadowed_at=now)
            )
        await self.session.execute(
            update(VisitPassORM)
            .where(VisitPassORM.created_at < cutoff, VisitPassORM.shadowed_at.is_(None))
            .values(vehicle_plate=None, shadowed_at=now)
        )
        await self.session.flush()
        return len(targets)

    async def log_data_access(self, entry: dict[str, Any]) -> None:
        self.session.add(DataAccessLogORM(**entry))
        await self.session.flush()

    async def save_entry_event(self, event: EntryEvent) -> EntryEvent:
        self.session.add(
            EntryEventORM(
                villa_id=event.villa_id,
                at=event.at,
                person_phone=event.person_phone,
                person_name=event.person_name,
                lots=[lot.model_dump() for lot in event.lots],
                method=str(event.method),
                guard=event.guard,
                vehicle_plate=event.vehicle_plate,
                plate_hmac=event.plate_hmac,
            )
        )
        await self.session.flush()
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
        stmt = select(EntryEventORM).where(EntryEventORM.villa_id == normalize_villa_id(villa_id))
        if person_phone:
            stmt = stmt.where(EntryEventORM.person_phone == person_phone)
        if plate_hmac:
            stmt = stmt.where(EntryEventORM.plate_hmac == plate_hmac)
        if date_from:
            stmt = stmt.where(EntryEventORM.at >= date_from)
        if date_to:
            stmt = stmt.where(EntryEventORM.at <= date_to)
        # lot_id vive en JSON: se filtra en Python sobre un lote acotado
        stmt = stmt.order_by(EntryEventORM.at.desc()).limit(limit if not lot_id else limit * 5)
        rows = (await self.session.execute(stmt)).scalars().all()
        events = [
            EntryEvent(
                villa_id=r.villa_id,
                at=r.at,
                person_phone=r.person_phone,
                person_name=r.person_name,
                lots=[EntryLot(**lot) for lot in (r.lots or [])],
                method=EntryMethod(r.method),
                guard=r.guard,
                vehicle_plate=r.vehicle_plate,
                plate_hmac=r.plate_hmac,
            )
            for r in rows
        ]
        if lot_id:
            events = [e for e in events if any(lot.lot_id == lot_id for lot in e.lots)]
        return events[:limit]
