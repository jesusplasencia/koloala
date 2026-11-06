from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.audit import get_client_ip, get_user_agent
from app.core.config import settings
from app.core.security import GuardOrAdminDep, ResidentIdentityDep
from app.db.repositories import PostgresAccessRepository
from app.db.session import AsyncSessionLocal
from app.models.access import (
    CreatePassRequest,
    GatehouseVerificationResponse,
    GuestPass,
    VerifyPassRequest,
)
from app.repositories.access_repo import in_memory_repo
from app.services.access_service import AccessService

router = APIRouter(prefix="/passes", tags=["Gatehouse Passes"])


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
    Usado por el residente autenticado para crear un código/QR temporal (Delivery, Taxi o Visita).
    Tanto la Villa (ej. VC3) como el Lote (ej. B14) se extraen directamente del Token JWT.
    """
    assert resident.lot_id is not None
    return await service.create_pass(
        villa_id=resident.villa_id,
        authenticated_lot_id=resident.lot_id,
        payload=payload,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/verify", response_model=GatehouseVerificationResponse)
async def verify_pass_at_gate(
    payload: VerifyPassRequest,
    request: Request,
    guard_identity: GuardOrAdminDep,
    service: AccessServiceDep,
) -> GatehouseVerificationResponse:
    """
    Usado por el vigilante en garita: valida el código/QR de 6 caracteres dentro de su Villa
    y cierra el registro capturando DNI + Placa del vehículo.
    """
    return await service.verify_at_gatehouse(
        villa_id=guard_identity.villa_id,
        payload=payload,
        guard_username=guard_identity.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
