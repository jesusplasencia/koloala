from fastapi import HTTPException, status

from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
)
from app.repositories.lots_repo import LotRepositoryProtocol


class LotsService:
    def __init__(self, repo: LotRepositoryProtocol) -> None:
        self.repo = repo

    async def list_lots(self) -> list[PropertyLot]:
        return await self.repo.list_lots()

    async def get_lot(self, lot_id: str) -> PropertyLot:
        lot = await self.repo.get_lot(lot_id)
        if not lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{lot_id.upper()}' no existe en el padrón de Villa Club 3.",
            )
        return lot

    async def update_lot_maintenance(
        self, lot_id: str, payload: UpdateLotMaintenanceRequest
    ) -> PropertyLot:
        updated_lot = await self.repo.update_lot_status(
            lot_id=lot_id,
            is_maintenance_up_to_date=payload.is_maintenance_up_to_date,
            owner_name=payload.owner_name,
        )
        if not updated_lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{lot_id.upper()}' no existe en el padrón de Villa Club 3.",
            )
        return updated_lot

    async def bulk_upsert_lots(
        self, payload: BulkUpsertLotsRequest
    ) -> BulkUpsertLotsResponse:
        created_count, updated_count, processed_lots = (
            await self.repo.upsert_bulk_lots(payload.lots)
        )
        return BulkUpsertLotsResponse(
            processed_count=len(processed_lots),
            created_count=created_count,
            updated_count=updated_count,
            lots=processed_lots,
        )
