import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.config import settings
from app.models.access import (
    CreatePassRequest,
    GatehouseVerificationResponse,
    GuestPass,
    PassStatus,
    PassType,
    VerifyPassRequest,
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

    def _default_visitor_info(self, pass_type: PassType) -> str:
        match pass_type:
            case PassType.DELIVERY:
                return "Delivery Autorizado"
            case PassType.TAXI:
                return "Taxi Autorizado"
            case PassType.GUEST:
                return "Visita Autorizada"

    async def create_pass(
        self,
        authenticated_lot_id: str,
        payload: CreatePassRequest,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GuestPass:
        lot = await self.repo.get_lot(authenticated_lot_id)
        if not lot:
            log_audit_event(
                event="PASS_CREATION_DENIED_LOT_NOT_FOUND",
                actor=authenticated_lot_id,
                client_ip=client_ip,
                user_agent=user_agent,
                details={"lot_id": authenticated_lot_id},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{authenticated_lot_id}' no existe en el padrón de Villa Club 3.",
            )

        # REGLA DE ORO: Si no paga mantenimiento, no tiene acceso automatizado en garita
        if not lot.is_maintenance_up_to_date:
            log_audit_event(
                event="PASS_CREATION_DENIED_DELINQUENT",
                actor=lot.lot_id,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "lot_id": lot.lot_id,
                    "pass_type": str(payload.pass_type),
                    "reason": "MAINTENANCE_DEBT",
                },
            )
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
        visitor_info = (
            payload.visitor_info.strip()
            if payload.visitor_info and payload.visitor_info.strip()
            else self._default_visitor_info(payload.pass_type)
        )

        new_pass = GuestPass(
            code=self._generate_short_code(),
            lot_id=lot.lot_id,
            pass_type=payload.pass_type,
            visitor_info=visitor_info,
            status=PassStatus.ACTIVE,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl),
        )
        saved_pass = await self.repo.save_pass(new_pass)

        log_audit_event(
            event="PASS_CREATED",
            actor=lot.lot_id,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "code": saved_pass.code,
                "lot_id": saved_pass.lot_id,
                "pass_type": str(saved_pass.pass_type),
                "visitor_info": saved_pass.visitor_info,
                "expires_at": saved_pass.expires_at.isoformat(),
            },
        )
        return saved_pass

    async def verify_at_gatehouse(
        self,
        payload: VerifyPassRequest,
        guard_username: str = "GUARD",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GatehouseVerificationResponse:
        code = payload.code.upper()
        cleaned_dni = payload.visitor_dni.strip().upper()
        cleaned_plate = (
            payload.vehicle_plate.strip().upper()
            if payload.vehicle_plate and payload.vehicle_plate.strip()
            else None
        )

        guest_pass = await self.repo.get_pass_by_code(code)
        if not guest_pass:
            log_audit_event(
                event="GATE_VERIFY_DENIED_NOT_FOUND",
                actor=guard_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "code": code,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El código no existe.",
            )

        now = datetime.now(UTC)

        if guest_pass.status == PassStatus.USED:
            log_audit_event(
                event="GATE_VERIFY_DENIED_ALREADY_USED",
                actor=guard_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: Este pase ya fue utilizado anteriormente.",
                lot_id=guest_pass.lot_id,
            )

        if now > guest_pass.expires_at:
            guest_pass.status = PassStatus.EXPIRED
            await self.repo.save_pass(guest_pass)
            log_audit_event(
                event="GATE_VERIFY_DENIED_EXPIRED",
                actor=guard_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El pase ha expirado.",
                lot_id=guest_pass.lot_id,
            )

        lot = await self.repo.get_lot(guest_pass.lot_id)
        if not lot or not lot.is_maintenance_up_to_date:
            log_audit_event(
                event="GATE_VERIFY_DENIED_LOT_DELINQUENT",
                actor=guard_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El lote ya no cuenta con habilitación de mantenimiento.",
                lot_id=guest_pass.lot_id,
            )

        # REGLA DE GARITA: Todo TAXI o DELIVERY motorizado requiere placa obligatoria.
        # No quemamos el pase para que el vigilante digite la placa y vuelva a verificar.
        if (
            guest_pass.pass_type in (PassType.TAXI, PassType.DELIVERY)
            and not cleaned_plate
        ):
            log_audit_event(
                event="GATE_VERIFY_MISSING_PLATE",
                actor=guard_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "code": code,
                    "lot_id": lot.lot_id,
                    "pass_type": str(guest_pass.pass_type),
                    "visitor_dni": cleaned_dni,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message=(
                    f"FALTA PLACA: Para {guest_pass.pass_type} es obligatorio registrar "
                    "la placa del vehículo/moto además del DNI antes de abrir la reja."
                ),
                lot_id=lot.lot_id,
                owner_name=lot.owner_name,
                pass_type=guest_pass.pass_type,
                visitor_info=guest_pass.visitor_info,
            )

        # Closure en Garita: Quemar el pase de un solo uso guardando DNI y Placa
        guest_pass.status = PassStatus.USED
        guest_pass.used_at = now
        guest_pass.checked_in_dni = cleaned_dni
        guest_pass.checked_in_plate = cleaned_plate
        await self.repo.save_pass(guest_pass)

        log_audit_event(
            event="GATE_VERIFY_ALLOWED",
            actor=guard_username,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "code": guest_pass.code,
                "lot_id": lot.lot_id,
                "owner_name": lot.owner_name,
                "pass_type": str(guest_pass.pass_type),
                "visitor_info": guest_pass.visitor_info,
                "checked_in_dni": cleaned_dni,
                "checked_in_plate": cleaned_plate,
            },
        )

        return GatehouseVerificationResponse(
            authorized=True,
            message=f"AUTORIZADO: Dejar pasar hacia {lot.lot_id} ({guest_pass.pass_type}).",
            lot_id=lot.lot_id,
            owner_name=lot.owner_name,
            pass_type=guest_pass.pass_type,
            visitor_info=guest_pass.visitor_info,
            checked_in_dni=cleaned_dni,
            checked_in_plate=cleaned_plate,
        )
