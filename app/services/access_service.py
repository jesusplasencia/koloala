import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from app.core.config import settings
from app.models.access import (
    CreatePassRequest,
    GatehouseVerificationResponse,
    GuestPass,
    PassStatus,
    PassType,
)
from app.repositories.access_repo import AccessRepositoryProtocol


class AccessService:
    def __init__(self, repo: AccessRepositoryProtocol) -> None:
        self.repo = repo

    def _generate_short_code(self, length: int = 6) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _get_ttl_minutes(self, pass_type: PassType) -> int:
        match pass_type:
            case PassType.DELIVERY:
                return settings.delivery_pass_ttl_minutes
            case PassType.TAXI:
                return settings.taxi_pass_ttl_minutes
            case PassType.GUEST:
                return settings.guest_pass_ttl_minutes

    async def create_pass(self, payload: CreatePassRequest) -> GuestPass:
        lot = await self.repo.get_lot(payload.lot_id)
        if not lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{payload.lot_id}' no existe en el padrón de Villa Club 3.",
            )

        # REGLA DE ORO: Si no paga mantenimiento, no tiene acceso automatizado en garita
        if not lot.is_maintenance_up_to_date:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Lote {lot.lot_id} registra deuda de mantenimiento. "
                    "No puede generar pases automáticos para taxis/delivery; "
                    "el residente debe acercarse personalmente a abrir la reja."
                ),
            )

        now = datetime.now(UTC)
        ttl = self._get_ttl_minutes(payload.pass_type)

        new_pass = GuestPass(
            code=self._generate_short_code(),
            lot_id=lot.lot_id,
            pass_type=payload.pass_type,
            visitor_info=payload.visitor_info,
            status=PassStatus.ACTIVE,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl),
        )
        return await self.repo.save_pass(new_pass)

    async def verify_at_gatehouse(self, code: str) -> GatehouseVerificationResponse:
        guest_pass = await self.repo.get_pass_by_code(code)
        if not guest_pass:
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El código no existe.",
            )

        now = datetime.now(UTC)

        if guest_pass.status == PassStatus.USED:
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: Este pase ya fue utilizado anteriormente.",
                lot_id=guest_pass.lot_id,
            )

        if now > guest_pass.expires_at:
            guest_pass.status = PassStatus.EXPIRED
            await self.repo.save_pass(guest_pass)
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El pase ha expirado.",
                lot_id=guest_pass.lot_id,
            )

        lot = await self.repo.get_lot(guest_pass.lot_id)
        if not lot or not lot.is_maintenance_up_to_date:
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El lote ya no cuenta con habilitación de mantenimiento.",
                lot_id=guest_pass.lot_id,
            )

        # Quemar el pase de un solo uso y autorizar apertura de reja
        guest_pass.status = PassStatus.USED
        guest_pass.used_at = now
        await self.repo.save_pass(guest_pass)

        return GatehouseVerificationResponse(
            authorized=True,
            message=f"AUTORIZADO: Dejar pasar hacia {lot.lot_id} ({guest_pass.pass_type}).",
            lot_id=lot.lot_id,
            owner_name=lot.owner_name,
            pass_type=guest_pass.pass_type,
            visitor_info=guest_pass.visitor_info,
        )
