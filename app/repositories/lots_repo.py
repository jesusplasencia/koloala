"""Lotes físicos + membresías persona<->lote. Nombre, celular y DNI viven SOLO en people."""

from datetime import UTC, datetime
from typing import Protocol

from app.models.lot import (
    BulkLotItem,
    LotMembership,
    LotType,
    PropertyLot,
    normalize_lot_id,
    normalize_villa_id,
)
from app.models.user import MembershipRole, Person
from app.repositories.people_repo import (
    InMemoryPeopleRepository,
    PeopleRepositoryProtocol,
    in_memory_people_repo,
)


class LotRepositoryProtocol(Protocol):
    people: PeopleRepositoryProtocol

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None: ...
    async def list_lots(
        self, villa_id: str, page: int = 0, limit: int = 50, is_maintenance_up_to_date: bool | None = None
    ) -> tuple[list[PropertyLot], int]: ...
    async def update_lot_status(self, villa_id: str, lot_id: str, is_maintenance_up_to_date: bool) -> PropertyLot | None: ...
    async def upsert_bulk_lots(self, villa_id: str, items: list[BulkLotItem]) -> tuple[int, int, list[PropertyLot]]: ...
    async def get_membership(self, villa_id: str, lot_id: str, phone: str) -> LotMembership | None: ...
    async def list_memberships_by_phone(self, villa_id: str, phone: str) -> list[LotMembership]: ...
    async def list_memberships_by_person(self, villa_id: str, person_id: str) -> list[LotMembership]: ...
    async def list_lot_members(self, villa_id: str, lot_id: str) -> list[LotMembership]: ...
    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        person_id: str,
        membership_role: MembershipRole,
        added_by_person_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership: ...
    async def deactivate_membership(self, villa_id: str, lot_id: str, person_id: str) -> LotMembership | None: ...


class _Mem:
    def __init__(self, villa_id: str, lot_id: str, person_id: str, role: MembershipRole, added_by: str | None) -> None:
        now = datetime.now(UTC)
        self.villa_id = villa_id
        self.lot_id = lot_id
        self.person_id = person_id
        self.role = role
        self.added_by = added_by
        self.is_active = True
        self.created_at = now
        self.updated_at = now


class InMemoryLotRepository:
    def __init__(self, people: InMemoryPeopleRepository | None = None, seed: bool = True) -> None:
        self.people = people if people is not None else in_memory_people_repo
        self._lots: dict[tuple[str, str], PropertyLot] = {}
        self._mems: dict[tuple[str, str, str], _Mem] = {}
        if seed:
            self._seed()

    def _seed(self) -> None:
        now = datetime.now(UTC)
        for villa, lot, ok in (("VC3", "B14", True), ("VC3", "F08", False), ("VC2", "B14", False)):
            self._lots[(villa, lot)] = PropertyLot(
                villa_id=villa, lot_id=lot, is_maintenance_up_to_date=ok, created_at=now, updated_at=now
            )
        for villa, lot, phone in (("VC3", "B14", "916220518"), ("VC3", "F08", "999111222"), ("VC2", "B14", "988777666")):
            person = next((p for p in self.people._people.values() if p.phone == phone), None)
            if person:
                self._mems[(villa, lot, person.id)] = _Mem(villa, lot, person.id, MembershipRole.ANCHOR, None)

    @property
    def lots_store(self) -> dict[tuple[str, str], PropertyLot]:
        return self._lots

    # ---------- vistas ----------
    async def _to_membership(self, m: _Mem) -> LotMembership:
        person = await self.people.get_by_id(m.person_id)
        added_by = await self.people.get_by_id(m.added_by) if m.added_by else None
        lot = self._lots.get((m.villa_id, m.lot_id))
        return LotMembership(
            villa_id=m.villa_id,
            lot_id=m.lot_id,
            person_id=m.person_id,
            phone=person.phone if person else "",
            full_name=(person.full_name if person else None) or "Sin nombre",
            document_id=person.document_id if person else None,
            membership_role=m.role,
            added_by=added_by.phone if added_by else "ADMIN",
            is_active=m.is_active,
            is_maintenance_up_to_date=lot.is_maintenance_up_to_date if lot else None,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def _with_owner(self, lot: PropertyLot) -> PropertyLot:
        anchors = sorted(
            (m for m in self._mems.values() if (m.villa_id, m.lot_id) == (lot.villa_id, lot.lot_id) and m.is_active and m.role == MembershipRole.ANCHOR),
            key=lambda m: m.created_at,
        )
        owner: Person | None = await self.people.get_by_id(anchors[0].person_id) if anchors else None
        return lot.model_copy(
            update={
                "owner_name": owner.full_name if owner else (lot.display_name if lot.lot_type != LotType.LOT else None),
                "owner_phone": owner.phone if owner else None,
            }
        )

    # ---------- lotes ----------
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        lot = self._lots.get((normalize_villa_id(villa_id), normalize_lot_id(lot_id)))
        return await self._with_owner(lot) if lot else None

    async def list_lots(
        self, villa_id: str, page: int = 0, limit: int = 50, is_maintenance_up_to_date: bool | None = None
    ) -> tuple[list[PropertyLot], int]:
        norm = normalize_villa_id(villa_id)
        items = sorted(
            (
                lot
                for (v, _), lot in self._lots.items()
                if v == norm and (is_maintenance_up_to_date is None or lot.is_maintenance_up_to_date == is_maintenance_up_to_date)
            ),
            key=lambda lot: lot.lot_id,
        )
        page_items = items[page * limit : (page + 1) * limit]
        return [await self._with_owner(lot) for lot in page_items], len(items)

    async def update_lot_status(self, villa_id: str, lot_id: str, is_maintenance_up_to_date: bool) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        lot = self._lots.get(key)
        if not lot:
            return None
        lot.is_maintenance_up_to_date = is_maintenance_up_to_date
        lot.updated_at = datetime.now(UTC)
        return await self._with_owner(lot)

    async def upsert_bulk_lots(self, villa_id: str, items: list[BulkLotItem]) -> tuple[int, int, list[PropertyLot]]:
        norm = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        created = updated = 0
        processed: list[PropertyLot] = []
        for item in items:
            key = (norm, normalize_lot_id(item.lot_id))
            lot = self._lots.get(key)
            if lot:
                lot.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                lot.updated_at = now
                updated += 1
            else:
                lot = PropertyLot(
                    villa_id=norm, lot_id=key[1], is_maintenance_up_to_date=item.is_maintenance_up_to_date, created_at=now, updated_at=now
                )
                self._lots[key] = lot
                created += 1
            if item.owner_phone:
                person = await self.people.upsert_person(
                    item.owner_phone, item.owner_name, item.owner_document_id, overwrite=True
                )
                await self.upsert_membership(norm, key[1], person.id, MembershipRole.ANCHOR)
            processed.append(await self._with_owner(lot))
        return created, updated, processed

    # ---------- membresías ----------
    async def get_membership(self, villa_id: str, lot_id: str, phone: str) -> LotMembership | None:
        person = await self.people.get_by_phone(phone)
        if not person:
            return None
        m = self._mems.get((normalize_villa_id(villa_id), normalize_lot_id(lot_id), person.id))
        return await self._to_membership(m) if m and m.is_active else None

    async def list_memberships_by_person(self, villa_id: str, person_id: str) -> list[LotMembership]:
        norm = normalize_villa_id(villa_id)
        mems = sorted(
            (m for m in self._mems.values() if m.villa_id == norm and m.person_id == str(person_id) and m.is_active),
            key=lambda m: m.created_at,
        )
        return [await self._to_membership(m) for m in mems]

    async def list_memberships_by_phone(self, villa_id: str, phone: str) -> list[LotMembership]:
        person = await self.people.get_by_phone(phone)
        return await self.list_memberships_by_person(villa_id, person.id) if person else []

    async def list_lot_members(self, villa_id: str, lot_id: str) -> list[LotMembership]:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        mems = sorted(
            (m for m in self._mems.values() if (m.villa_id, m.lot_id) == key and m.is_active),
            key=lambda m: (m.role != MembershipRole.ANCHOR, m.created_at),
        )
        return [await self._to_membership(m) for m in mems]

    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        person_id: str,
        membership_role: MembershipRole,
        added_by_person_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id), str(person_id))
        m = self._mems.get(key)
        if m:
            m.role = membership_role
            m.is_active = is_active
            if added_by_person_id:
                m.added_by = added_by_person_id
            m.updated_at = datetime.now(UTC)
        else:
            m = _Mem(key[0], key[1], key[2], membership_role, added_by_person_id)
            m.is_active = is_active
            self._mems[key] = m
        return await self._to_membership(m)

    async def deactivate_membership(self, villa_id: str, lot_id: str, person_id: str) -> LotMembership | None:
        m = self._mems.get((normalize_villa_id(villa_id), normalize_lot_id(lot_id), str(person_id)))
        if not m or not m.is_active:
            return None
        m.is_active = False
        m.updated_at = datetime.now(UTC)
        return await self._to_membership(m)


in_memory_lot_repo = InMemoryLotRepository()
