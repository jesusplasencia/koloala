from datetime import UTC, datetime
from typing import Protocol

from app.models.lot import (
    BulkLotItem,
    LotMembership,
    PropertyLot,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.models.user import MembershipRole


class LotRepositoryProtocol(Protocol):
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None: ...
    async def list_lots(
        self,
        villa_id: str,
        page: int = 0,
        limit: int = 50,
        is_maintenance_up_to_date: bool | None = None,
    ) -> tuple[list[PropertyLot], int]: ...
    async def update_lot_status(
        self,
        villa_id: str,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
        owner_phone: str | None = None,
        owner_email: str | None = None,
    ) -> PropertyLot | None: ...
    async def upsert_bulk_lots(
        self, villa_id: str, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot], list[PropertyLot]]: ...
    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None: ...
    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]: ...
    async def list_lot_members(
        self, villa_id: str, lot_id: str
    ) -> list[LotMembership]: ...
    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        phone: str,
        full_name: str,
        membership_role: MembershipRole,
        added_by: str = "ADMIN",
        document_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership: ...
    async def deactivate_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None: ...


class InMemoryLotRepository:
    def __init__(
        self, lots_store: dict[tuple[str, str], PropertyLot] | None = None
    ) -> None:
        now = datetime.now(UTC)
        # Llave compuesta: (villa_id, lot_id) -> ej. ('VC3', 'B14'), ('VC3', 'P20'), ('VC2', 'B14')
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = {
                ("VC3", "B14"): PropertyLot(
                    villa_id="VC3",
                    lot_id="B14",
                    owner_name="Jesus Plasencia",
                    owner_phone="916220518",
                    owner_email="jesus.b14@lasvillas.club",
                    is_maintenance_up_to_date=True,
                    created_at=now,
                    updated_at=now,
                ),
                ("VC3", "F08"): PropertyLot(
                    villa_id="VC3",
                    lot_id="F08",
                    owner_name="Vecino Moroso",
                    owner_phone="999111222",
                    owner_email=None,
                    is_maintenance_up_to_date=False,
                    created_at=now,
                    updated_at=now,
                ),
                ("VC2", "B14"): PropertyLot(
                    villa_id="VC2",
                    lot_id="B14",
                    owner_name="Propietario VC2 B14",
                    owner_phone="988777666",
                    owner_email="vc2.b14@lasvillas.club",
                    is_maintenance_up_to_date=False,
                    created_at=now,
                    updated_at=now,
                ),
            }

        # Tabla 3NF lot_memberships en memoria: llave compuesta (villa_id, lot_id, phone)
        self._memberships: dict[tuple[str, str, str], LotMembership] = {
            ("VC3", "B14", "916220518"): LotMembership(
                villa_id="VC3",
                lot_id="B14",
                phone="916220518",
                full_name="Jesus Plasencia",
                membership_role=MembershipRole.ANCHOR,
                added_by="916220518",
                is_active=True,
                is_maintenance_up_to_date=True,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "F08", "999111222"): LotMembership(
                villa_id="VC3",
                lot_id="F08",
                phone="999111222",
                full_name="Vecino Moroso",
                membership_role=MembershipRole.ANCHOR,
                added_by="ADMIN",
                is_active=True,
                is_maintenance_up_to_date=False,
                created_at=now,
                updated_at=now,
            ),
            ("VC2", "B14", "988777666"): LotMembership(
                villa_id="VC2",
                lot_id="B14",
                phone="988777666",
                full_name="Propietario VC2 B14",
                membership_role=MembershipRole.ANCHOR,
                added_by="ADMIN",
                is_active=True,
                is_maintenance_up_to_date=False,
                created_at=now,
                updated_at=now,
            ),
        }

    @property
    def lots_store(self) -> dict[tuple[str, str], PropertyLot]:
        return self._lots

    @property
    def memberships_store(self) -> dict[tuple[str, str, str], LotMembership]:
        return self._memberships

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        return self._lots.get(key)

    async def list_lots(
        self,
        villa_id: str,
        page: int = 0,
        limit: int = 50,
        is_maintenance_up_to_date: bool | None = None,
    ) -> tuple[list[PropertyLot], int]:
        norm_villa = normalize_villa_id(villa_id)
        filtered = [
            lot
            for lot in self._lots.values()
            if lot.villa_id == norm_villa
            and (
                is_maintenance_up_to_date is None
                or lot.is_maintenance_up_to_date == is_maintenance_up_to_date
            )
        ]
        filtered.sort(key=lambda x: x.lot_id)
        total = len(filtered)
        offset = page * limit
        paginated_items = filtered[offset : offset + limit]
        return paginated_items, total

    async def update_lot_status(
        self,
        villa_id: str,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
        owner_phone: str | None = None,
        owner_email: str | None = None,
    ) -> PropertyLot | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        key = (norm_villa, norm_lot)
        lot = self._lots.get(key)
        if not lot:
            return None

        lot.is_maintenance_up_to_date = is_maintenance_up_to_date
        if owner_name is not None:
            lot.owner_name = owner_name
        if owner_phone is not None:
            lot.owner_phone = owner_phone
        if owner_email is not None:
            lot.owner_email = owner_email
        lot.updated_at = datetime.now(UTC)

        # Sincronizar automáticamente la membresía ANCHOR si tiene teléfono
        norm_phone = normalize_phone_username(lot.owner_phone)
        if norm_phone:
            await self.upsert_membership(
                villa_id=norm_villa,
                lot_id=norm_lot,
                phone=norm_phone,
                full_name=lot.owner_name,
                membership_role=MembershipRole.ANCHOR,
                added_by="ADMIN",
            )
        return lot

    async def upsert_bulk_lots(
        self, villa_id: str, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot], list[PropertyLot]]:
        norm_villa = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        created_count = 0
        updated_count = 0
        processed: list[PropertyLot] = []
        newly_created_lots: list[PropertyLot] = []

        for item in items:
            normalized_id = normalize_lot_id(item.lot_id)
            key = (norm_villa, normalized_id)
            existing = self._lots.get(key)
            if existing:
                existing.owner_name = item.owner_name
                if item.owner_phone is not None:
                    existing.owner_phone = item.owner_phone
                if item.owner_email is not None:
                    existing.owner_email = item.owner_email
                existing.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                existing.updated_at = now
                processed.append(existing)
                updated_count += 1
                target_lot = existing
            else:
                new_lot = PropertyLot(
                    villa_id=norm_villa,
                    lot_id=normalized_id,
                    owner_name=item.owner_name,
                    owner_phone=item.owner_phone,
                    owner_email=item.owner_email,
                    is_maintenance_up_to_date=item.is_maintenance_up_to_date,
                    created_at=now,
                    updated_at=now,
                )
                self._lots[key] = new_lot
                processed.append(new_lot)
                newly_created_lots.append(new_lot)
                created_count += 1
                target_lot = new_lot

            norm_phone = normalize_phone_username(target_lot.owner_phone)
            if norm_phone:
                await self.upsert_membership(
                    villa_id=norm_villa,
                    lot_id=normalized_id,
                    phone=norm_phone,
                    full_name=target_lot.owner_name,
                    membership_role=MembershipRole.ANCHOR,
                    added_by="ADMIN",
                )

        return created_count, updated_count, processed, newly_created_lots

    def _enrich_membership(self, mem: LotMembership) -> LotMembership:
        lot = self._lots.get((mem.villa_id, mem.lot_id))
        mem.is_maintenance_up_to_date = lot.is_maintenance_up_to_date if lot else None
        return mem

    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        mem = self._memberships.get((norm_villa, norm_lot, norm_phone))
        if not mem or not mem.is_active:
            return None
        return self._enrich_membership(mem)

    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        results = [
            self._enrich_membership(mem)
            for mem in self._memberships.values()
            if mem.villa_id == norm_villa and mem.phone == norm_phone and mem.is_active
        ]
        # Ordenar poniendo primero los lotes donde es ANCHOR y luego alfabéticamente por lote
        results.sort(
            key=lambda m: (0 if m.membership_role == MembershipRole.ANCHOR else 1, m.lot_id)
        )
        return results

    async def list_lot_members(
        self, villa_id: str, lot_id: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        results = [
            self._enrich_membership(mem)
            for mem in self._memberships.values()
            if mem.villa_id == norm_villa and mem.lot_id == norm_lot and mem.is_active
        ]
        results.sort(
            key=lambda m: (0 if m.membership_role == MembershipRole.ANCHOR else 1, m.full_name)
        )
        return results

    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        phone: str,
        full_name: str,
        membership_role: MembershipRole,
        added_by: str = "ADMIN",
        document_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        now = datetime.now(UTC)
        key = (norm_villa, norm_lot, norm_phone)
        existing = self._memberships.get(key)
        if existing:
            existing.full_name = full_name
            existing.membership_role = membership_role
            existing.added_by = added_by
            if document_id is not None:
                existing.document_id = document_id
            existing.is_active = is_active
            existing.updated_at = now
            return self._enrich_membership(existing)

        new_mem = LotMembership(
            villa_id=norm_villa,
            lot_id=norm_lot,
            phone=norm_phone,
            full_name=full_name,
            document_id=document_id,
            membership_role=membership_role,
            added_by=added_by,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        self._memberships[key] = new_mem
        return self._enrich_membership(new_mem)

    async def deactivate_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        key = (norm_villa, norm_lot, norm_phone)
        existing = self._memberships.get(key)
        if not existing or not existing.is_active:
            return None
        existing.is_active = False
        existing.updated_at = datetime.now(UTC)
        return self._enrich_membership(existing)


# Instancia singleton del padrón de lotes en memoria
in_memory_lot_repo = InMemoryLotRepository()
