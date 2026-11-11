import secrets
import string
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.config import settings
from app.core.security import AuthenticatedIdentity
from app.models.access import (
    CreatePassRequest,
    DailyLogResponse,
    GatehouseVerificationResponse,
    GuestPass,
    PassStatus,
    PassType,
    VerifyPassRequest,
)
from app.models.lot import normalize_lot_id, normalize_villa_id
from app.models.user import MembershipRole, UserRole
from app.models.villa_services import VISITOR_SERVICES_ENABLED, find_villa_service
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
            case PassType.RESIDENT_QR:
                return settings.guest_pass_ttl_minutes
            case PassType.COMMERCE:
                return 60

    def _default_visitor_info(self, pass_type: PassType) -> str:
        match pass_type:
            case PassType.DELIVERY:
                return "Delivery Autorizado"
            case PassType.TAXI:
                return "Taxi Autorizado"
            case PassType.GUEST:
                return "Visita Autorizada"
            case PassType.RESIDENT_QR:
                return "Ingreso Personal Residente"
            case PassType.COMMERCE:
                return "Visita a Comercio / Servicio Interno"

    async def create_pass(
        self,
        villa_id: str,
        authenticated_lot_id: str | None,
        payload: CreatePassRequest,
        identity: AuthenticatedIdentity | None = None,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GuestPass:
        norm_villa = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        ttl = self._get_ttl_minutes(payload.pass_type)

        # 1. Caso VISITOR o Pase a Comercio/Servicio Interno (Panadería, Botica, Parque, Canchita)
        if payload.pass_type == PassType.COMMERCE or (
            identity and identity.role == UserRole.VISITOR and not payload.lot_id
        ):
            raw_dest = (
                payload.destination.strip()
                if payload.destination and payload.destination.strip()
                else "VISITA"
            )
            if VISITOR_SERVICES_ENABLED:
                service = find_villa_service(norm_villa, raw_dest)
                if not service:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"El servicio '{raw_dest}' no existe en {norm_villa}.",
                    )
                dest_label = service.code
            else:
                # SHADOW: sin catálogo, todo visitante entra como VISITA genérica trazable
                dest_label = "VISITA"
            visitor_info = (
                payload.visitor_info.strip()
                if payload.visitor_info and payload.visitor_info.strip()
                else f"Ingreso a {dest_label}"
            )
            creator_phone = identity.username if identity else None
            creator_name = (identity.full_name or identity.username) if identity else "Visitante"
            new_pass = GuestPass(
                villa_id=norm_villa,
                code=self._generate_short_code(),
                lot_id=dest_label[:20],
                pass_type=PassType.COMMERCE,
                visitor_info=visitor_info,
                destination=dest_label,
                created_by_phone=creator_phone,
                created_by_name=creator_name,
                created_by_role="VISITOR",
                status=PassStatus.ACTIVE,
                created_at=now,
                expires_at=now + timedelta(minutes=ttl),
            )
            saved_pass = await self.repo.save_pass(new_pass)
            log_audit_event(
                event="PASS_CREATED_COMMERCE_VISITOR",
                actor=f"{norm_villa}:{creator_phone or 'VISITOR'}",
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    "villa_id": norm_villa,
                    "code": saved_pass.code,
                    "destination": saved_pass.destination,
                    "created_by_phone": saved_pass.created_by_phone,
                    "created_by_name": saved_pass.created_by_name,
                },
            )
            return saved_pass

        # 2. Caso Residente (ANCHOR o HOUSEHOLD) con soporte Multi-Lote (3NF)
        target_lot_id = (
            normalize_lot_id(payload.lot_id)
            if payload.lot_id and payload.lot_id.strip()
            else (
                normalize_lot_id(authenticated_lot_id)
                if authenticated_lot_id
                else None
            )
        )

        membership = None
        if identity:
            # Si especificó un lot_id distinto o si se autentica por teléfono, verificamos su membresía 3NF
            if target_lot_id:
                membership = await self.repo.get_membership(
                    norm_villa, target_lot_id, identity.username
                )
            else:
                all_mems = await self.repo.list_memberships_by_phone(
                    norm_villa, identity.username
                )
                if all_mems:
                    membership = all_mems[0]
                    target_lot_id = membership.lot_id

            # Si el usuario pidió explícitamente un lot_id que NO es su lote ni tiene membresía 3NF en él
            if (
                payload.lot_id
                and not membership
                and identity.lot_id != target_lot_id
                and identity.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"No tienes membresía activa (ANCHOR ni HOUSEHOLD) sobre el lote "
                        f"'{target_lot_id}' en {norm_villa}."
                    ),
                )

            # Identidad telefónica (no admin): la membresía 3NF ACTIVA es la única fuente de verdad.
            # Un HOUSEHOLD dado de baja conserva su JWT (30 días) con lot_id, pero ya no puede
            # generar pases para ese lote.
            is_phone_identity = (
                identity.username.isdigit() and len(identity.username) == 9
            )
            if (
                is_phone_identity
                and not membership
                and identity.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"No tienes membresía activa (ANCHOR ni HOUSEHOLD) sobre el lote "
                        f"'{target_lot_id}' en {norm_villa}."
                    ),
                )

        if not target_lot_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Debes indicar el lote para el cual deseas generar el pase o seleccionar un servicio interno.",
            )

        lot = await self.repo.get_lot(norm_villa, target_lot_id)
        if not lot:
            log_audit_event(
                event="PASS_CREATION_DENIED_LOT_NOT_FOUND",
                actor=f"{norm_villa}:{target_lot_id}",
                client_ip=client_ip,
                user_agent=user_agent,
                details={"villa_id": norm_villa, "lot_id": target_lot_id},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{target_lot_id}' no existe en el padrón de {norm_villa}.",
            )

        # REGLA DE ORO: Si el lote no paga mantenimiento, no tiene acceso automatizado para terceros
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

        visitor_info = (
            payload.visitor_info.strip()
            if payload.visitor_info and payload.visitor_info.strip()
            else self._default_visitor_info(payload.pass_type)
        )

        # Trazabilidad forense de quién generó el QR (ANCHOR vs HOUSEHOLD)
        if membership:
            creator_phone = membership.phone
            creator_name = membership.full_name
            creator_role = str(membership.membership_role)
        elif identity:
            creator_phone = identity.username
            creator_name = identity.full_name or lot.owner_name
            creator_role = (
                str(identity.membership_role)
                if identity.membership_role
                else str(MembershipRole.ANCHOR)
            )
        else:
            creator_phone = lot.owner_phone or lot.lot_id
            creator_name = lot.owner_name
            creator_role = str(MembershipRole.ANCHOR)

        new_pass = GuestPass(
            villa_id=norm_villa,
            code=self._generate_short_code(),
            lot_id=lot.lot_id,
            pass_type=payload.pass_type,
            visitor_info=visitor_info,
            destination=f"Lote {lot.lot_id}",
            created_by_phone=creator_phone,
            created_by_name=creator_name,
            created_by_role=creator_role,
            status=PassStatus.ACTIVE,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl),
        )
        saved_pass = await self.repo.save_pass(new_pass)

        log_audit_event(
            event="PASS_CREATED",
            actor=f"{norm_villa}:{creator_phone}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "code": saved_pass.code,
                "lot_id": saved_pass.lot_id,
                "pass_type": str(saved_pass.pass_type),
                "visitor_info": saved_pass.visitor_info,
                "created_by_phone": saved_pass.created_by_phone,
                "created_by_name": saved_pass.created_by_name,
                "created_by_role": saved_pass.created_by_role,
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
        cleaned_dni = (
            payload.visitor_dni.strip().upper()
            if payload.visitor_dni and payload.visitor_dni.strip()
            else "VERIFICADO"
        )
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
                destination=guest_pass.destination,
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
                destination=guest_pass.destination,
            )

        owner_display_name = guest_pass.created_by_name or "Visitante"
        if guest_pass.pass_type != PassType.COMMERCE:
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
                    destination=guest_pass.destination,
                )
            owner_display_name = lot.owner_name

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
                    "lot_id": guest_pass.lot_id,
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
                lot_id=guest_pass.lot_id,
                destination=guest_pass.destination,
                owner_name=owner_display_name,
                pass_type=guest_pass.pass_type,
                visitor_info=guest_pass.visitor_info,
                created_by_phone=guest_pass.created_by_phone,
                created_by_name=guest_pass.created_by_name,
                created_by_role=guest_pass.created_by_role,
            )

        # Closure en Garita: Quemar el pase de un solo uso guardando DNI, Placa y Vigilante
        guest_pass.status = PassStatus.USED
        guest_pass.used_at = now
        guest_pass.checked_in_dni = cleaned_dni
        guest_pass.checked_in_plate = cleaned_plate
        guest_pass.checked_in_by_guard = guard_username
        await self.repo.save_pass(guest_pass)

        log_audit_event(
            event="GATE_VERIFY_ALLOWED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "code": guest_pass.code,
                "lot_id": guest_pass.lot_id,
                "destination": guest_pass.destination,
                "owner_name": owner_display_name,
                "pass_type": str(guest_pass.pass_type),
                "visitor_info": guest_pass.visitor_info,
                "created_by_phone": guest_pass.created_by_phone,
                "created_by_name": guest_pass.created_by_name,
                "created_by_role": guest_pass.created_by_role,
                "checked_in_dni": cleaned_dni,
                "checked_in_plate": cleaned_plate,
            },
        )

        return GatehouseVerificationResponse(
            authorized=True,
            message=f"AUTORIZADO ({norm_villa}): Dejar pasar hacia {guest_pass.lot_id} ({guest_pass.pass_type}).",
            villa_id=norm_villa,
            lot_id=guest_pass.lot_id,
            destination=guest_pass.destination,
            owner_name=owner_display_name,
            pass_type=guest_pass.pass_type,
            visitor_info=guest_pass.visitor_info,
            created_by_phone=guest_pass.created_by_phone,
            created_by_name=guest_pass.created_by_name,
            created_by_role=guest_pass.created_by_role,
            checked_in_dni=cleaned_dni,
            checked_in_plate=cleaned_plate,
            checked_in_by_guard=guard_username,
        )

    async def get_daily_log(
        self, villa_id: str, limit: int = 200
    ) -> DailyLogResponse:
        norm_villa = normalize_villa_id(villa_id)
        items = await self.repo.list_passes_for_villa(norm_villa, limit=limit)
        checked_in = sum(1 for p in items if p.status == PassStatus.USED)
        active_pending = sum(1 for p in items if p.status == PassStatus.ACTIVE)
        return DailyLogResponse(
            villa_id=norm_villa,
            total_passes=len(items),
            checked_in_count=checked_in,
            active_pending_count=active_pending,
            items=items,
        )
