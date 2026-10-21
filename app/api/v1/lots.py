from typing import Annotated

from fastapi import APIRouter, Depends

from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
)
from app.repositories.lots_repo import in_memory_lot_repo
from app.services.lots_service import LotsService

router = APIRouter(prefix="/lots", tags=["Maintenance of Lots"])


def get_lots_service() -> LotsService:
    return LotsService(repo=in_memory_lot_repo)


LotsServiceDep = Annotated[LotsService, Depends(get_lots_service)]


@router.get("", response_model=list[PropertyLot])
async def list_all_lots(service: LotsServiceDep) -> list[PropertyLot]:
    """Usado por el administrador en su UI para ver el padrón completo y estado de pagos."""
    return await service.list_lots()


@router.get("/{lot_id}", response_model=PropertyLot)
async def get_lot_detail(lot_id: str, service: LotsServiceDep) -> PropertyLot:
    """Consulta el estado actual de mantenimiento de un lote específico."""
    return await service.get_lot(lot_id)


@router.patch("/{lot_id}/maintenance", response_model=PropertyLot)
async def update_single_lot_maintenance(
    lot_id: str,
    payload: UpdateLotMaintenanceRequest,
    service: LotsServiceDep,
) -> PropertyLot:
    """Permite al administrador habilitar o pasar a mora un lote individual desde la UI."""
    return await service.update_lot_maintenance(lot_id, payload)


@router.put("/bulk", response_model=BulkUpsertLotsResponse)
async def bulk_sync_lots(
    payload: BulkUpsertLotsRequest,
    service: LotsServiceDep,
) -> BulkUpsertLotsResponse:
    """
    Carga o actualiza en bloque una lista de lotes (Upsert).
    Ideal para importar el cierre mensual de cobranza de Villa Club 3.
    """
    return await service.bulk_upsert_lots(payload)
