from datetime import UTC, datetime
from typing import Protocol

from app.models.lot import (
    BulkLotItem,
    PropertyLot,
    normalize_lot_id,
    normalize_villa_id,
)


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
        owner_email: str | None = None,
    ) -> PropertyLot | None: ...
    async def upsert_bulk_lots(
        self, villa_id: str, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot], list[PropertyLot]]: ...


class InMemoryLotRepository:
    def __init__(
        self, lots_store: dict[tuple[str, str], PropertyLot] | None = None
    ) -> None:
        now = datetime.now(UTC)
        # Llave compuesta: (villa_id, lot_id) -> ej. ('VC3', 'B14'), ('VC2', 'B14')
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = {
                ("VC3", "B14"): PropertyLot(
                    villa_id="VC3",
                    lot_id="B14",
                    owner_name="Jesus Plasencia",
                    owner_email="jesus.b14@lasvillas.club",
                    is_maintenance_up_to_date=True,
                    created_at=now,
                    updated_at=now,
                ),
                ("VC3", "F08"): PropertyLot(
                    villa_id="VC3",
                    lot_id="F08",
                    owner_name="Vecino Moroso",
                    owner_email="vecino.f08@lasvillas.club",
                    is_maintenance_up_to_date=False,
                    created_at=now,
                    updated_at=now,
                ),
                # En Villa Club 2 (VC2), el lote B14 pertenece a otra familia y está en mora
                ("VC2", "B14"): PropertyLot(
                    villa_id="VC2",
                    lot_id="B14",
                    owner_name="Propietario VC2 B14",
                    owner_email="vc2.b14@lasvillas.club",
                    is_maintenance_up_to_date=False,
                    created_at=now,
                    updated_at=now,
                ),
            }

    @property
    def lots_store(self) -> dict[tuple[str, str], PropertyLot]:
        return self._lots

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
        # Orden determinístico por lot_id (A01, B14, F08, P20...) esencial para paginación
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
        owner_email: str | None = None,
    ) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        lot = self._lots.get(key)
        if not lot:
            return None

        lot.is_maintenance_up_to_date = is_maintenance_up_to_date
        if owner_name is not None:
            lot.owner_name = owner_name
        if owner_email is not None:
            lot.owner_email = owner_email
        lot.updated_at = datetime.now(UTC)
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
                if item.owner_email is not None:
                    existing.owner_email = item.owner_email
                existing.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                existing.updated_at = now
                processed.append(existing)
                updated_count += 1
            else:
                new_lot = PropertyLot(
                    villa_id=norm_villa,
                    lot_id=normalized_id,
                    owner_name=item.owner_name,
                    owner_email=item.owner_email,
                    is_maintenance_up_to_date=item.is_maintenance_up_to_date,
                    created_at=now,
                    updated_at=now,
                )
                self._lots[key] = new_lot
                processed.append(new_lot)
                newly_created_lots.append(new_lot)
                created_count += 1

        return created_count, updated_count, processed, newly_created_lots


# Instancia singleton del padrón de lotes en memoria
in_memory_lot_repo = InMemoryLotRepository()
