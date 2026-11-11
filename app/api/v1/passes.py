from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.audit import get_client_ip, get_user_agent
from app.core.config import settings
from app.core.security import GuardOrAdminDep, ResidentIdentityDep
from app.db.repositories import PostgresAccessRepository
from app.db.session import AsyncSessionLocal
from app.models.access import (
    CreatePassRequest,
    DailyLogResponse,
    GatehouseVerificationResponse,
    GuestPass,
    VerifyPassRequest,
)
from app.repositories.access_repo import in_memory_repo
from app.services.access_service import AccessService

router = APIRouter(prefix="/passes", tags=["Gatehouse Passes & Daily Log"])


async def get_access_service() -> AsyncIterator[AccessService]:
    if not settings.use_postgres:
        yield AccessService(repo=in_memory_repo)
        return
    async with AsyncSessionLocal() as session:
        try:
            yield AccessService(repo=PostgresAccessRepository(session))
            await session.commit()
        except Exception:
            await session.rollback()
            raise


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


@router.post("", response_model=GuestPass, status_code=status.HTTP_201_CREATED)
async def generate_access_pass(
    payload: CreatePassRequest,
    request: Request,
    resident: ResidentIdentityDep,
    service: AccessServiceDep,
) -> GuestPass:
    """
    Usado por ANCHOR, HOUSEHOLD o VISITOR para crear un código/QR temporal:
    - Para ANCHOR/HOUSEHOLD: permite seleccionar a cuál de sus lotes autorizados corresponde (`lot_id`)
      y registra quién lo generó (`created_by_phone`, `created_by_name`, `created_by_role`).
    - Para VISITOR: permite generar su QR hacia un servicio interno (`PANADERIA`, `BOTICA`, `PARQUE`, `CANCHITA`).
    """
    return await service.create_pass(
        villa_id=resident.villa_id,
        authenticated_lot_id=resident.lot_id,
        payload=payload,
        identity=resident,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/daily-log", response_model=DailyLogResponse)
async def get_villa_daily_visitors_log(
    guard_or_admin: GuardOrAdminDep,
    service: AccessServiceDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=500,
            description="Cantidad máxima de ingresos/pases recientes de la Villa a mostrar",
        ),
    ] = 200,
) -> DailyLogResponse:
    """
    [SOLO GARITA (GUARD) O ADMINISTRACIÓN (ADMIN / SUPER_ADMIN)]
    Devuelve la bitácora en vivo de visitantes, residentes y clientes de comercios internos
    del día para la Villa actual, con el rol, celular/nombre de quien generó el QR, DNI y placa.
    """
    return await service.get_daily_log(
        villa_id=guard_or_admin.villa_id,
        limit=limit,
    )


@router.post("/verify", response_model=GatehouseVerificationResponse)
async def verify_pass_at_gate(
    payload: VerifyPassRequest,
    request: Request,
    guard_identity: GuardOrAdminDep,
    service: AccessServiceDep,
) -> GatehouseVerificationResponse:
    """
    Usado por el vigilante en garita (móvil o escritorio) o el administrador:
    valida el código/QR de 6 caracteres dentro de su Villa y cierra el registro
    capturando DNI + Placa del vehículo.
    """
    return await service.verify_at_gatehouse(
        villa_id=guard_identity.villa_id,
        payload=payload,
        guard_username=guard_identity.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
