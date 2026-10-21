from typing import Annotated

from fastapi import APIRouter, Depends, status

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
    service: AccessServiceDep,
) -> GuestPass:
    """Usado por el residente para crear un código temporal para su delivery o taxi."""
    return await service.create_pass(payload)


@router.post("/verify", response_model=GatehouseVerificationResponse)
async def verify_pass_at_gate(
    payload: VerifyPassRequest,
    service: AccessServiceDep,
) -> GatehouseVerificationResponse:
    """Usado por el vigilante en garita para validar el código antes de abrir la reja."""
    return await service.verify_at_gatehouse(payload.code)
