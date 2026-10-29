from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.audit import get_client_ip, get_user_agent
from app.core.security import AdminDep
from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    PaginatedLotsResponse,
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


@router.get("", response_model=PaginatedLotsResponse)
async def list_all_lots(
    admin: AdminDep,
    service: LotsServiceDep,
    page: Annotated[
        int,
        Query(
            ge=0,
            description="Número de página empezando desde 0 (page=0 son los primeros 50 lotes)",
        ),
    ] = 0,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Cantidad de lotes por página (por defecto 50, máximo 100)",
        ),
    ] = 50,
    is_maintenance_up_to_date: Annotated[
        bool | None,
        Query(
            description="Opcional: filtrar solo lotes al día (true) o solo morosos (false)",
        ),
    ] = None,
) -> PaginatedLotsResponse:
    """
    [SOLO ADMIN] Lista de forma paginada el padrón de lotes de la Villa del Administrador
    (50 por página por defecto, empezando desde page=0).
    """
    return await service.list_lots(
        villa_id=admin.villa_id,
        page=page,
        limit=limit,
        is_maintenance_up_to_date=is_maintenance_up_to_date,
    )


@router.get("/{lot_id}", response_model=PropertyLot)
async def get_lot_detail(
    lot_id: str,
    admin: AdminDep,
    service: LotsServiceDep,
) -> PropertyLot:
    """[SOLO ADMIN] Consulta el estado actual de un lote dentro de su Villa (ej. VC3 + B14)."""
    return await service.get_lot(admin.villa_id, lot_id)


@router.patch("/{lot_id}/maintenance", response_model=PropertyLot)
async def update_single_lot_maintenance(
    lot_id: str,
    payload: UpdateLotMaintenanceRequest,
    request: Request,
    admin: AdminDep,
    service: LotsServiceDep,
) -> PropertyLot:
    """[SOLO ADMIN] Habilita o pasa a mora un lote individual dentro de su Villa."""
    return await service.update_lot_maintenance(
        villa_id=admin.villa_id,
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
    [SOLO ADMIN] Carga o actualiza en bloque la lista de lotes de la Villa del Administrador.
    Para cada lote nuevo, autogenera su usuario, clave inicial y URL (ej. https://vc3.lasvillas.club).
    """
    return await service.bulk_upsert_lots(
        villa_id=admin.villa_id,
        payload=payload,
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
