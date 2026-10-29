from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.audit import get_client_ip, get_user_agent
from app.core.security import AdminDep
from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
)
from app.repositories.lots_repo import in_memory_lot_repo
from app.repositories.users_repo import in_memory_user_repo
from app.services.lots_service import LotsService

router = APIRouter(prefix="/lots", tags=["Maintenance of Lots"])


def get_lots_service() -> LotsService:
    return LotsService(repo=in_memory_lot_repo, user_repo=in_memory_user_repo)


LotsServiceDep = Annotated[LotsService, Depends(get_lots_service)]


@router.get("", response_model=list[PropertyLot])
async def list_all_lots(
    _admin: AdminDep,
    service: LotsServiceDep,
) -> list[PropertyLot]:
    """[SOLO ADMIN] Lista el padrón completo de lotes y su estado de mantenimiento."""
    return await service.list_lots()


@router.get("/{lot_id}", response_model=PropertyLot)
async def get_lot_detail(
    lot_id: str,
    _admin: AdminDep,
    service: LotsServiceDep,
) -> PropertyLot:
    """[SOLO ADMIN] Consulta el estado actual de mantenimiento de un lote específico (ej. B14)."""
    return await service.get_lot(lot_id)


@router.patch("/{lot_id}/maintenance", response_model=PropertyLot)
async def update_single_lot_maintenance(
    lot_id: str,
    payload: UpdateLotMaintenanceRequest,
    request: Request,
    admin: AdminDep,
    service: LotsServiceDep,
) -> PropertyLot:
    """[SOLO ADMIN] Habilita o pasa a mora un lote individual desde la UI."""
    return await service.update_lot_maintenance(
        lot_id=lot_id,
        payload=payload,
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.put("/bulk", response_model=BulkUpsertLotsResponse)
async def bulk_sync_lots(
    payload: BulkUpsertLotsRequest,
    request: Request,
    admin: AdminDep,
    service: LotsServiceDep,
) -> BulkUpsertLotsResponse:
    """
    [SOLO ADMIN] Carga o actualiza en bloque una lista de lotes (Upsert).
    Para cada lote nuevo, autogenera su usuario y contraseña inicial para enviar por WhatsApp.
    """
    return await service.bulk_upsert_lots(
        payload=payload,
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
