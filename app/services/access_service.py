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
from app.models.lot import normalize_villa_id
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
        villa_id: str,
        authenticated_lot_id: str,
        payload: CreatePassRequest,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GuestPass:
        norm_villa = normalize_villa_id(villa_id)
        lot = await self.repo.get_lot(norm_villa, authenticated_lot_id)
        if not lot:
            log_audit_event(
                event="PASS_CREATION_DENIED_LOT_NOT_FOUND",
                actor=f"{norm_villa}:{authenticated_lot_id}",
                client_ip=client_ip,
                user_agent=user_agent,
                details={"villa_id": norm_villa, "lot_id": authenticated_lot_id},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{authenticated_lot_id}' no existe en el padrón de {norm_villa}.",
            )

        # REGLA DE ORO: Si no paga mantenimiento, no tiene acceso automatizado en garita
        if not lot.is_maintenance_up_to_date:
            log_audit_event(
                event="PASS_CREATION_DENIED_DELINQUENT",
                actor=f"{norm_villa}:{lot.lot_id}",
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "lot_id": lot.lot_id,
                    "pass_type": str(payload.pass_type),
                    "reason": "MAINTENANCE_DEBT",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Lote {lot.lot_id} ({norm_villa}) registra deuda de mantenimiento. "
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
            villa_id=norm_villa,
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
            actor=f"{norm_villa}:{lot.lot_id}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
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
        villa_id: str,
        payload: VerifyPassRequest,
        guard_username: str = "GUARD",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GatehouseVerificationResponse:
        norm_villa = normalize_villa_id(villa_id)
        actor_label = f"{norm_villa}:{guard_username}"
        code = payload.code.upper()
        cleaned_dni = payload.visitor_dni.strip().upper()
        cleaned_plate = (
            payload.vehicle_plate.strip().upper()
            if payload.vehicle_plate and payload.vehicle_plate.strip()
            else None
        )

        guest_pass = await self.repo.get_pass_by_code(norm_villa, code)
        if not guest_pass:
            log_audit_event(
                event="GATE_VERIFY_DENIED_NOT_FOUND",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "code": code,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message=f"DENEGADO: El código no existe en {norm_villa}.",
                villa_id=norm_villa,
            )

        now = datetime.now(UTC)

        if guest_pass.status == PassStatus.USED:
            log_audit_event(
                event="GATE_VERIFY_DENIED_ALREADY_USED",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: Este pase ya fue utilizado anteriormente.",
                villa_id=norm_villa,
                lot_id=guest_pass.lot_id,
            )

        if now > guest_pass.expires_at:
            guest_pass.status = PassStatus.EXPIRED
            await self.repo.save_pass(guest_pass)
            log_audit_event(
                event="GATE_VERIFY_DENIED_EXPIRED",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El pase ha expirado.",
                villa_id=norm_villa,
                lot_id=guest_pass.lot_id,
            )

        lot = await self.repo.get_lot(norm_villa, guest_pass.lot_id)
        if not lot or not lot.is_maintenance_up_to_date:
            log_audit_event(
                event="GATE_VERIFY_DENIED_LOT_DELINQUENT",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "code": code,
                    "lot_id": guest_pass.lot_id,
                    "visitor_dni": cleaned_dni,
                    "vehicle_plate": cleaned_plate,
                },
            )
            return GatehouseVerificationResponse(
                authorized=False,
                message="DENEGADO: El lote ya no cuenta con habilitación de mantenimiento.",
                villa_id=norm_villa,
                lot_id=guest_pass.lot_id,
            )

        # REGLA DE GARITA: Todo TAXI o DELIVERY motorizado requiere placa obligatoria.
        if (
            guest_pass.pass_type in (PassType.TAXI, PassType.DELIVERY)
            and not cleaned_plate
        ):
            log_audit_event(
                event="GATE_VERIFY_MISSING_PLATE",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
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
                villa_id=norm_villa,
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
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
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
            message=f"AUTORIZADO ({norm_villa}): Dejar pasar hacia {lot.lot_id} ({guest_pass.pass_type}).",
            villa_id=norm_villa,
            lot_id=lot.lot_id,
            owner_name=lot.owner_name,
            pass_type=guest_pass.pass_type,
            visitor_info=guest_pass.visitor_info,
            checked_in_dni=cleaned_dni,
            checked_in_plate=cleaned_plate,
        )
