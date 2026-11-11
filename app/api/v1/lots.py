from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.audit import get_client_ip, get_user_agent
from app.core.config import settings
from app.core.security import AdminDep, CurrentIdentityDep
from app.db.repositories import PostgresLotRepository, PostgresUserRepository
from app.db.session import AsyncSessionLocal
from app.models.lot import (
    AddHouseholdMemberRequest,
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    GeneratedLotCredential,
    LotMembership,
    PaginatedLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
)
from app.repositories.lots_repo import in_memory_lot_repo
from app.repositories.users_repo import in_memory_user_repo
from app.services.lots_service import LotsService

router = APIRouter(prefix="/lots", tags=["Maintenance of Lots & 3NF Memberships"])


async def get_lots_service() -> AsyncIterator[LotsService]:
    if not settings.use_postgres:
        yield LotsService(repo=in_memory_lot_repo, user_repo=in_memory_user_repo)
        return
    # Unit of Work: LotRepository y UserRepository comparten la misma transacción atómica
    async with AsyncSessionLocal() as session:
        try:
            yield LotsService(
                repo=PostgresLotRepository(session),
                user_repo=PostgresUserRepository(session),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


LotsServiceDep = Annotated[LotsService, Depends(get_lots_service)]


@router.get("/my-memberships", response_model=list[LotMembership])
async def get_my_lot_memberships(
    identity: CurrentIdentityDep,
    service: LotsServiceDep,
) -> list[LotMembership]:
    """
    [RESIDENTE / ADMIN] Devuelve todos los lotes donde la persona autenticada
    es ANCHOR (Persona-Ancla) o HOUSEHOLD (Conviviente) dentro de la Villa actual.
    Permite seleccionar fácilmente entre varios lotes (ej. P20 y Q02).
    """
    return await service.list_my_memberships(identity)


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
    """[SOLO ADMIN] Habilita o pasa a mora un lote individual (y opcionalmente actualiza teléfono/email)."""
    return await service.update_lot_maintenance(
        villa_id=admin.villa_id,
        lot_id=lot_id,
        payload=payload,
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/{lot_id}/reset-password", response_model=GeneratedLotCredential)
async def reset_lot_resident_password(
    lot_id: str,
    request: Request,
    admin: AdminDep,
    service: LotsServiceDep,
) -> GeneratedLotCredential:
    """
    [SOLO ADMIN] Genera una nueva contraseña numérica de 8 dígitos para un lote
    (ideal cuando un vecino olvida su clave o cambia de celular) y devuelve el link de WhatsApp.
    """
    return await service.reset_lot_password(
        villa_id=admin.villa_id,
        lot_id=lot_id,
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
    [SOLO ADMIN] Carga o actualiza en bloque la lista de lotes de la Villa (con teléfono y/o email opcionales).
    Para cada lote nuevo, autogenera su clave de 8 dígitos y link directo de WhatsApp (wa.me).
    """
    return await service.bulk_upsert_lots(
        villa_id=admin.villa_id,
        payload=payload,
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/{lot_id}/members", response_model=list[LotMembership])
async def list_lot_members(
    lot_id: str,
    identity: CurrentIdentityDep,
    service: LotsServiceDep,
) -> list[LotMembership]:
    """
    [ANCHOR / HOUSEHOLD / ADMIN] Lista los integrantes activos (Persona-Ancla y Convivientes)
    asociados al lote especificado en la tabla 3NF lot_memberships.
    """
    return await service.list_lot_members(
        villa_id=identity.villa_id,
        lot_id=lot_id,
        identity=identity,
    )


@router.post(
    "/{lot_id}/members",
    response_model=LotMembership,
    status_code=status.HTTP_201_CREATED,
)
async def add_lot_household_member(
    lot_id: str,
    payload: AddHouseholdMemberRequest,
    request: Request,
    identity: CurrentIdentityDep,
    service: LotsServiceDep,
) -> LotMembership:
    """
    [SOLO ANCHOR DEL LOTE O ADMIN] Agrega un integrante HOUSEHOLD (ej. 'Angel Plasencia', '980180219')
    al lote. Un integrante con rol HOUSEHOLD tiene estrictamente prohibido agregar o borrar miembros (403).
    """
    return await service.add_lot_member(
        villa_id=identity.villa_id,
        lot_id=lot_id,
        payload=payload,
        identity=identity,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.delete("/{lot_id}/members/{member_phone}", response_model=LotMembership)
async def remove_lot_household_member(
    lot_id: str,
    member_phone: str,
    request: Request,
    identity: CurrentIdentityDep,
    service: LotsServiceDep,
) -> LotMembership:
    """
    [SOLO ANCHOR DEL LOTE O ADMIN] Retira (desactiva vía UPDATE is_active=False) a un integrante
    HOUSEHOLD del lote. Un HOUSEHOLD recibe 403 Forbidden si intenta borrar a alguien del lote.
    """
    return await service.deactivate_lot_member(
        villa_id=identity.villa_id,
        lot_id=lot_id,
        member_phone=member_phone,
        identity=identity,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
