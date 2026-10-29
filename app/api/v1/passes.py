from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.audit import get_client_ip, get_user_agent
from app.core.security import GuardOrAdminDep, ResidentLotDep
from app.models.access import (
    CreatePassRequest,
    GatehouseVerificationResponse,
    GuestPass,
    VerifyPassRequest,
)
from app.repositories.access_repo import in_memory_repo
from app.services.access_service import AccessService

router = APIRouter(prefix="/passes", tags=["Gatehouse Passes"])


def get_access_service() -> AccessService:
    return AccessService(repo=in_memory_repo)


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


@router.post("", response_model=GuestPass, status_code=status.HTTP_201_CREATED)
async def generate_access_pass(
    payload: CreatePassRequest,
    request: Request,
    authenticated_lot_id: ResidentLotDep,
    service: AccessServiceDep,
) -> GuestPass:
    """
    Usado por el residente autenticado para crear un código/QR temporal (Delivery, Taxi o Visita).
    El lote se toma directamente del Token JWT del vecino (imposible falsificar otro lote).
    """
    return await service.create_pass(
        authenticated_lot_id=authenticated_lot_id,
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
    Usado por el vigilante en garita: valida el código/QR de 6 caracteres y cierra el registro
    capturando obligatoriamente DNI + Placa del vehículo.
    """
    return await service.verify_at_gatehouse(
        payload=payload,
        guard_username=guard_identity.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
