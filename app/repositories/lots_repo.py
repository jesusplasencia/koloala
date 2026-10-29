from datetime import UTC, datetime
from typing import Protocol

from app.models.lot import BulkLotItem, PropertyLot, normalize_lot_id


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
    ) -> tuple[int, int, list[PropertyLot], list[str]]: ...


class InMemoryLotRepository:
    def __init__(self, lots_store: dict[str, PropertyLot] | None = None) -> None:
        now = datetime.now(UTC)
        # Datos semilla en el formato oficial corto de Villa Club 3: B14 y F08
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = {
                "B14": PropertyLot(
                    lot_id="B14",
                    owner_name="Jesus Plasencia",
                    is_maintenance_up_to_date=True,
                    created_at=now,
                    updated_at=now,
                ),
                "F08": PropertyLot(
                    lot_id="F08",
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
        return self._lots.get(normalize_lot_id(lot_id))

    async def list_lots(self) -> list[PropertyLot]:
        return list(self._lots.values())

    async def update_lot_status(
        self,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
    ) -> PropertyLot | None:
        normalized_id = normalize_lot_id(lot_id)
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
    ) -> tuple[int, int, list[PropertyLot], list[str]]:
        now = datetime.now(UTC)
        created_count = 0
        updated_count = 0
        processed: list[PropertyLot] = []
        newly_created_lot_ids: list[str] = []

        for item in items:
            normalized_id = normalize_lot_id(item.lot_id)
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
                newly_created_lot_ids.append(normalized_id)
                created_count += 1

        return created_count, updated_count, processed, newly_created_lot_ids


# Instancia singleton del padrón de lotes en memoria
in_memory_lot_repo = InMemoryLotRepository()
