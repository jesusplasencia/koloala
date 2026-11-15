"""Repositorio Postgres de lotes y membresías (persona<->lote). Los datos personales viven en people."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LotMembershipORM, PersonORM, PropertyLotORM
from app.db.people_repository import PostgresPeopleRepository
from app.models.lot import (
    BulkLotItem,
    LotMembership,
    LotType,
    PropertyLot,
    normalize_lot_id,
    normalize_villa_id,
)
from app.models.user import MembershipRole
from app.repositories.people_repo import norm_phone


def lot_to_domain(row: PropertyLotORM, owner: PersonORM | None = None) -> PropertyLot:
    lot_type = LotType(row.lot_type or "LOT")
    return PropertyLot(
        villa_id=row.villa_id,
        lot_id=row.lot_id,
        owner_name=owner.full_name if owner else (row.display_name if lot_type != LotType.LOT else None),
        owner_phone=owner.phone if owner else None,
        is_maintenance_up_to_date=row.is_maintenance_up_to_date,
        lot_type=lot_type,
        display_name=row.display_name,
        is_public=bool(row.is_public),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresLotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.people = PostgresPeopleRepository(session)

    # ---------- helpers ----------
    async def _owner(self, villa_id: str, lot_id: str) -> PersonORM | None:
        stmt = (
            select(PersonORM)
            .join(LotMembershipORM, LotMembershipORM.person_id == PersonORM.id)
            .where(
                LotMembershipORM.villa_id == villa_id,
                LotMembershipORM.lot_id == lot_id,
                LotMembershipORM.is_active.is_(True),
                LotMembershipORM.membership_role == str(MembershipRole.ANCHOR),
            )
            .order_by(LotMembershipORM.created_at)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _mem_domain(self, mem: LotMembershipORM, person: PersonORM) -> LotMembership:
        lot = await self.session.get(PropertyLotORM, (mem.villa_id, mem.lot_id))
        added_by = await self.session.get(PersonORM, mem.added_by_person_id) if mem.added_by_person_id else None
        return LotMembership(
            villa_id=mem.villa_id,
            lot_id=mem.lot_id,
            person_id=str(person.id),
            phone=person.phone,
            full_name=person.full_name or "Sin nombre",
            document_id=person.document_id,
            membership_role=MembershipRole(mem.membership_role),
            added_by=added_by.phone if added_by else "ADMIN",
            is_active=mem.is_active,
            is_maintenance_up_to_date=lot.is_maintenance_up_to_date if lot else None,
            created_at=mem.created_at,
            updated_at=mem.updated_at,
        )

    async def _mems(self, *conditions) -> list[LotMembership]:
        stmt = (
            select(LotMembershipORM, PersonORM)
            .join(PersonORM, PersonORM.id == LotMembershipORM.person_id)
            .where(LotMembershipORM.is_active.is_(True), *conditions)
            .order_by(LotMembershipORM.membership_role, LotMembershipORM.created_at)
        )
        rows = (await self.session.execute(stmt)).all()
        return [await self._mem_domain(m, p) for m, p in rows]

    # ---------- lotes ----------
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        row = await self.session.get(PropertyLotORM, key)
        return lot_to_domain(row, await self._owner(*key)) if row else None

    async def list_lots(
        self, villa_id: str, page: int = 0, limit: int = 50, is_maintenance_up_to_date: bool | None = None
    ) -> tuple[list[PropertyLot], int]:
        norm = normalize_villa_id(villa_id)
        conditions = [PropertyLotORM.villa_id == norm]
        if is_maintenance_up_to_date is not None:
            conditions.append(PropertyLotORM.is_maintenance_up_to_date == is_maintenance_up_to_date)
        total = int((await self.session.execute(select(func.count()).select_from(PropertyLotORM).where(*conditions))).scalar_one())
        stmt = select(PropertyLotORM).where(*conditions).order_by(PropertyLotORM.lot_id).offset(page * limit).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [lot_to_domain(r, await self._owner(r.villa_id, r.lot_id)) for r in rows], total

    async def update_lot_status(self, villa_id: str, lot_id: str, is_maintenance_up_to_date: bool) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        row = await self.session.get(PropertyLotORM, key)
        if not row:
            return None
        row.is_maintenance_up_to_date = is_maintenance_up_to_date
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return lot_to_domain(row, await self._owner(*key))

    async def upsert_bulk_lots(self, villa_id: str, items: list[BulkLotItem]) -> tuple[int, int, list[PropertyLot]]:
        norm = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        created = updated = 0
        processed: list[PropertyLot] = []
        for item in items:
            key = (norm, normalize_lot_id(item.lot_id))
            row = await self.session.get(PropertyLotORM, key)
            if row:
                row.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                row.updated_at = now
                updated += 1
            else:
                row = PropertyLotORM(
                    villa_id=key[0], lot_id=key[1], is_maintenance_up_to_date=item.is_maintenance_up_to_date, created_at=now, updated_at=now
                )
                self.session.add(row)
                created += 1
            await self.session.flush()
            if item.owner_phone:
                person = await self.people.upsert_person(item.owner_phone, item.owner_name, item.owner_document_id, overwrite=False)
                await self.upsert_membership(norm, key[1], person.id, MembershipRole.ANCHOR)
            processed.append(lot_to_domain(row, await self._owner(*key)))
        return created, updated, processed

    # ---------- membresías ----------
    async def get_membership(self, villa_id: str, lot_id: str, phone: str) -> LotMembership | None:
        found = await self._mems(
            LotMembershipORM.villa_id == normalize_villa_id(villa_id),
            LotMembershipORM.lot_id == normalize_lot_id(lot_id),
            PersonORM.phone == norm_phone(phone),
        )
        return found[0] if found else None

    async def list_memberships_by_person(self, villa_id: str, person_id: str) -> list[LotMembership]:
        try:
            pid = uuid.UUID(str(person_id))
        except ValueError:
            return []
        return await self._mems(LotMembershipORM.villa_id == normalize_villa_id(villa_id), LotMembershipORM.person_id == pid)

    async def list_memberships_by_phone(self, villa_id: str, phone: str) -> list[LotMembership]:
        return await self._mems(LotMembershipORM.villa_id == normalize_villa_id(villa_id), PersonORM.phone == norm_phone(phone))

    async def list_lot_members(self, villa_id: str, lot_id: str) -> list[LotMembership]:
        return await self._mems(
            LotMembershipORM.villa_id == normalize_villa_id(villa_id),
            LotMembershipORM.lot_id == normalize_lot_id(lot_id),
        )

    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        person_id: str,
        membership_role: MembershipRole,
        added_by_person_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id), uuid.UUID(str(person_id)))
        now = datetime.now(UTC)
        row = await self.session.get(LotMembershipORM, key)
        if row:
            row.membership_role = str(membership_role)
            row.is_active = is_active
            if added_by_person_id:
                row.added_by_person_id = uuid.UUID(str(added_by_person_id))
            row.updated_at = now
        else:
            row = LotMembershipORM(
                villa_id=key[0],
                lot_id=key[1],
                person_id=key[2],
                membership_role=str(membership_role),
                added_by_person_id=uuid.UUID(str(added_by_person_id)) if added_by_person_id else None,
                is_active=is_active,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        await self.session.flush()
        person = await self.session.get(PersonORM, key[2])
        assert person is not None
        return await self._mem_domain(row, person)

    async def deactivate_membership(self, villa_id: str, lot_id: str, person_id: str) -> LotMembership | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id), uuid.UUID(str(person_id)))
        row = await self.session.get(LotMembershipORM, key)
        if not row or not row.is_active:
            return None
        row.is_active = False
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        person = await self.session.get(PersonORM, key[2])
        assert person is not None
        return await self._mem_domain(row, person)
