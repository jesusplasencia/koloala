from datetime import UTC, datetime
from typing import Protocol

from app.models.lot import BulkLotItem, PropertyLot


class LotRepositoryProtocol(Protocol):
    async def get_lot(self, lot_id: str) -> PropertyLot | None: ...
    async def list_lots(self) -> list[PropertyLot]: ...
    async def update_lot_status(
        self,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
    ) -> PropertyLot | None: ...
    async def upsert_bulk_lots(
        self, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot]]: ...


class InMemoryLotRepository:
    def __init__(self, lots_store: dict[str, PropertyLot] | None = None) -> None:
        now = datetime.now(UTC)
        # Si nos pasan un diccionario externo lo compartimos; si no, inicializamos el padrón semilla
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = {
                "MZ-B-L14": PropertyLot(
                    lot_id="MZ-B-L14",
                    owner_name="Jesus Plasencia",
                    is_maintenance_up_to_date=True,
                    created_at=now,
                    updated_at=now,
                ),
                "MZ-F-L08": PropertyLot(
                    lot_id="MZ-F-L08",
                    owner_name="Vecino Moroso",
                    is_maintenance_up_to_date=False,
                    created_at=now,
                    updated_at=now,
                ),
            }

    @property
    def lots_store(self) -> dict[str, PropertyLot]:
        return self._lots

    async def get_lot(self, lot_id: str) -> PropertyLot | None:
        return self._lots.get(lot_id.upper())

    async def list_lots(self) -> list[PropertyLot]:
        return list(self._lots.values())

    async def update_lot_status(
        self,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
    ) -> PropertyLot | None:
        normalized_id = lot_id.upper()
        lot = self._lots.get(normalized_id)
        if not lot:
            return None

        lot.is_maintenance_up_to_date = is_maintenance_up_to_date
        if owner_name is not None:
            lot.owner_name = owner_name
        lot.updated_at = datetime.now(UTC)
        return lot

    async def upsert_bulk_lots(
        self, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot]]:
        now = datetime.now(UTC)
        created_count = 0
        updated_count = 0
        processed: list[PropertyLot] = []

        for item in items:
            normalized_id = item.lot_id.upper()
            existing = self._lots.get(normalized_id)
            if existing:
                existing.owner_name = item.owner_name
                existing.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                existing.updated_at = now
                processed.append(existing)
                updated_count += 1
            else:
                new_lot = PropertyLot(
                    lot_id=normalized_id,
                    owner_name=item.owner_name,
                    is_maintenance_up_to_date=item.is_maintenance_up_to_date,
                    created_at=now,
                    updated_at=now,
                )
                self._lots[normalized_id] = new_lot
                processed.append(new_lot)
                created_count += 1

        return created_count, updated_count, processed


# Instancia singleton del padrón de lotes en memoria
in_memory_lot_repo = InMemoryLotRepository()
