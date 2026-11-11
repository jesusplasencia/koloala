import math

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.security import (
    AuthenticatedIdentity,
    generate_initial_lot_password,
    hash_password,
)
from app.models.lot import (
    AddHouseholdMemberRequest,
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    GeneratedLotCredential,
    LotMembership,
    PaginatedLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
    build_whatsapp_onboarding_url,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.models.user import MembershipRole, UserRole
from app.repositories.lots_repo import LotRepositoryProtocol
from app.repositories.users_repo import UserRepositoryProtocol, in_memory_user_repo


class LotsService:
    def __init__(
        self,
        repo: LotRepositoryProtocol,
        user_repo: UserRepositoryProtocol = in_memory_user_repo,
    ) -> None:
        self.repo = repo
        self.user_repo = user_repo

    async def list_lots(
        self,
        villa_id: str,
        page: int = 0,
        limit: int = 50,
        is_maintenance_up_to_date: bool | None = None,
    ) -> PaginatedLotsResponse:
        norm_villa = normalize_villa_id(villa_id)
        items, total = await self.repo.list_lots(
            villa_id=norm_villa,
            page=page,
            limit=limit,
            is_maintenance_up_to_date=is_maintenance_up_to_date,
        )
        total_pages = math.ceil(total / limit) if total > 0 else 0
        has_next = (page + 1) < total_pages
        return PaginatedLotsResponse(
            villa_id=norm_villa,
            items=items,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
            has_next=has_next,
        )

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot:
        norm_villa = normalize_villa_id(villa_id)
        norm_id = normalize_lot_id(lot_id)
        lot = await self.repo.get_lot(norm_villa, norm_id)
        if not lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{norm_id}' no existe en el padrón de {norm_villa}.",
            )
        return lot

    async def update_lot_maintenance(
        self,
        villa_id: str,
        lot_id: str,
        payload: UpdateLotMaintenanceRequest,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> PropertyLot:
        norm_villa = normalize_villa_id(villa_id)
        norm_id = normalize_lot_id(lot_id)
        updated_lot = await self.repo.update_lot_status(
            villa_id=norm_villa,
            lot_id=norm_id,
            is_maintenance_up_to_date=payload.is_maintenance_up_to_date,
            owner_name=payload.owner_name,
            owner_phone=payload.owner_phone,
            owner_email=payload.owner_email,
        )
        if not updated_lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{norm_id}' no existe en el padrón de {norm_villa}.",
            )

        log_audit_event(
            event="LOT_MAINTENANCE_UPDATED",
            actor=f"{norm_villa}:{actor}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "lot_id": updated_lot.lot_id,
                "is_maintenance_up_to_date": updated_lot.is_maintenance_up_to_date,
                "owner_name": updated_lot.owner_name,
                "owner_phone": updated_lot.owner_phone,
                "owner_email": updated_lot.owner_email,
            },
        )
        return updated_lot

    async def bulk_upsert_lots(
        self,
        villa_id: str,
        payload: BulkUpsertLotsRequest,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> BulkUpsertLotsResponse:
        norm_villa = normalize_villa_id(villa_id)
        created_count, updated_count, processed_lots, newly_created_lots = (
            await self.repo.upsert_bulk_lots(norm_villa, payload.lots)
        )

        generated_credentials: list[GeneratedLotCredential] = []
        login_url = f"https://{norm_villa.lower()}.lasvillas.club"

        for new_lot in newly_created_lots:
            initial_password = generate_initial_lot_password()
            _, created_user = await self.user_repo.create_or_get_resident_user(
                villa_id=norm_villa,
                lot_id=new_lot.lot_id,
                initial_password=initial_password,
                email=new_lot.owner_email,
                full_name=new_lot.owner_name,
            )
            # Además, si tiene celular (Persona-Ancla), pre-registramos su membresía 3NF
            # (la activación de su PIN por WhatsApp ocurrirá en su 1er ingreso si aún no tiene clave)
            if created_user:
                whatsapp_url = build_whatsapp_onboarding_url(
                    phone=new_lot.owner_phone,
                    villa_id=norm_villa,
                    lot_id=new_lot.lot_id,
                    password=initial_password,
                    login_url=login_url,
                )
                generated_credentials.append(
                    GeneratedLotCredential(
                        villa_id=norm_villa,
                        lot_id=new_lot.lot_id,
                        username=new_lot.lot_id,
                        owner_name=new_lot.owner_name,
                        owner_phone=new_lot.owner_phone,
                        owner_email=new_lot.owner_email,
                        initial_password=initial_password,
                        login_url=login_url,
                        whatsapp_share_url=whatsapp_url,
                    )
                )

        log_audit_event(
            event="LOTS_BULK_SYNC",
            actor=f"{norm_villa}:{actor}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "processed_count": len(processed_lots),
                "created_count": created_count,
                "updated_count": updated_count,
                "new_lot_ids": [lot.lot_id for lot in newly_created_lots],
            },
        )

        return BulkUpsertLotsResponse(
            villa_id=norm_villa,
            processed_count=len(processed_lots),
            created_count=created_count,
            updated_count=updated_count,
            lots=processed_lots,
            generated_credentials=generated_credentials,
        )

    async def reset_lot_password(
        self,
        villa_id: str,
        lot_id: str,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> GeneratedLotCredential:
        """
        Permite al Administrador generar una nueva contraseña numérica de 8 dígitos
        para un vecino que olvidó su clave o cambió de teléfono.
        """
        norm_villa = normalize_villa_id(villa_id)
        norm_id = normalize_lot_id(lot_id)
        lot = await self.repo.get_lot(norm_villa, norm_id)
        if not lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{norm_id}' no existe en el padrón de {norm_villa}.",
            )

        new_password = generate_initial_lot_password()
        new_hash = hash_password(new_password)
        existing_user = await self.user_repo.get_user(norm_villa, norm_id)
        if existing_user:
            await self.user_repo.update_password(
                norm_villa,
                norm_id,
                new_hash,
                clear_last_login=True,
            )
        else:
            await self.user_repo.create_or_get_resident_user(
                villa_id=norm_villa,
                lot_id=norm_id,
                initial_password=new_password,
                email=lot.owner_email,
                full_name=lot.owner_name,
            )

        # Si el lote tiene owner_phone (Persona-Ancla), también reseteamos su cuenta telefónica si existe
        norm_phone = normalize_phone_username(lot.owner_phone)
        if norm_phone:
            phone_user = await self.user_repo.get_user(norm_villa, norm_phone)
            if phone_user:
                await self.user_repo.update_password(
                    norm_villa,
                    norm_phone,
                    new_hash,
                    clear_last_login=True,
                )

        login_url = f"https://{norm_villa.lower()}.lasvillas.club"
        whatsapp_url = build_whatsapp_onboarding_url(
            phone=lot.owner_phone,
            villa_id=norm_villa,
            lot_id=lot.lot_id,
            password=new_password,
            login_url=login_url,
        )

        log_audit_event(
            event="LOT_PASSWORD_RESET_BY_ADMIN",
            actor=f"{norm_villa}:{actor}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": norm_villa, "lot_id": lot.lot_id},
        )

        return GeneratedLotCredential(
            villa_id=norm_villa,
            lot_id=lot.lot_id,
            username=norm_phone or lot.lot_id,
            owner_name=lot.owner_name,
            owner_phone=lot.owner_phone,
            owner_email=lot.owner_email,
            initial_password=new_password,
            login_url=login_url,
            whatsapp_share_url=whatsapp_url,
        )

    # -------------------------------------------------------------------------
    # GESTIÓN 3NF DE MEMBRESÍAS (ANCHOR & HOUSEHOLD)
    # -------------------------------------------------------------------------
    async def list_my_memberships(
        self, identity: AuthenticatedIdentity
    ) -> list[LotMembership]:
        """
        Devuelve todos los lotes donde la persona autenticada es ANCHOR o HOUSEHOLD
        dentro de la Villa actual (ej. Pepito Juárez en P20 y Q02; Pepita Reina en Q03 y P20).
        """
        memberships = await self.repo.list_memberships_by_phone(
            identity.villa_id, identity.username
        )
        if memberships:
            return memberships

        # Compatibilidad si ingresó con código de lote directo (ej. B14)
        if identity.lot_id:
            lot = await self.repo.get_lot(identity.villa_id, identity.lot_id)
            if lot:
                return [
                    LotMembership(
                        villa_id=lot.villa_id,
                        lot_id=lot.lot_id,
                        phone=lot.owner_phone or identity.username,
                        full_name=lot.owner_name,
                        membership_role=MembershipRole.ANCHOR,
                        added_by="ADMIN",
                        is_active=True,
                        is_maintenance_up_to_date=lot.is_maintenance_up_to_date,
                        created_at=lot.created_at,
                        updated_at=lot.updated_at,
                    )
                ]
        return []

    async def _require_lot_member_or_admin(
        self,
        identity: AuthenticatedIdentity,
        villa_id: str,
        lot_id: str,
        require_anchor: bool = False,
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)

        if identity.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            return None

        mem = await self.repo.get_membership(norm_villa, norm_lot, identity.username)
        if not mem and identity.lot_id == norm_lot and identity.username == norm_lot:
            # Cuenta legacy por lote directo -> tratada como ANCHOR de su lote
            return None

        if not mem:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tienes permisos ni membresía activa sobre el lote {norm_lot} en {norm_villa}.",
            )

        if require_anchor and mem.membership_role != MembershipRole.ANCHOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acción restringida: tu rol en el lote {norm_lot} es HOUSEHOLD (Conviviente). "
                    "Como HOUSEHOLD solo puedes generar códigos QR de acceso; "
                    "únicamente la Persona-Ancla (ANCHOR) o el Administrador pueden agregar o retirar integrantes del lote."
                ),
            )
        return mem

    async def list_lot_members(
        self,
        villa_id: str,
        lot_id: str,
        identity: AuthenticatedIdentity,
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        await self.get_lot(norm_villa, norm_lot)
        await self._require_lot_member_or_admin(
            identity, norm_villa, norm_lot, require_anchor=False
        )
        return await self.repo.list_lot_members(norm_villa, norm_lot)

    async def add_lot_member(
        self,
        villa_id: str,
        lot_id: str,
        payload: AddHouseholdMemberRequest,
        identity: AuthenticatedIdentity,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> LotMembership:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        await self.get_lot(norm_villa, norm_lot)
        await self._require_lot_member_or_admin(
            identity, norm_villa, norm_lot, require_anchor=True
        )

        norm_phone = normalize_phone_username(payload.phone)
        if not norm_phone or not norm_phone.isdigit() or len(norm_phone) != 9:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El número de teléfono debe tener 9 dígitos válidos (ej. 980180219).",
            )

        # Solo ADMIN o SUPER_ADMIN puede designar a otro ANCHOR; un ANCHOR siempre agrega HOUSEHOLD
        target_role = payload.membership_role
        if identity.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            target_role = MembershipRole.HOUSEHOLD
            # Un ANCHOR no puede degradar (vía upsert) a otra Persona-Ancla del mismo lote
            existing_mem = await self.repo.get_membership(norm_villa, norm_lot, norm_phone)
            if existing_mem and existing_mem.membership_role == MembershipRole.ANCHOR:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Esta persona ya es Persona-Ancla (ANCHOR) del lote; solo el Administrador puede cambiar su rol.",
                )

        cleaned_doc = payload.document_id.strip().upper() if payload.document_id else None
        membership = await self.repo.upsert_membership(
            villa_id=norm_villa,
            lot_id=norm_lot,
            phone=norm_phone,
            full_name=payload.full_name.strip(),
            membership_role=target_role,
            added_by=identity.username,
            document_id=cleaned_doc,
            is_active=True,
        )

        log_audit_event(
            event="LOT_MEMBER_ADDED",
            actor=f"{norm_villa}:{identity.username}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "lot_id": norm_lot,
                "member_phone": norm_phone,
                "member_name": membership.full_name,
                "membership_role": str(membership.membership_role),
            },
        )
        return membership

    async def deactivate_lot_member(
        self,
        villa_id: str,
        lot_id: str,
        member_phone: str,
        identity: AuthenticatedIdentity,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> LotMembership:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_target_phone = normalize_phone_username(member_phone) or member_phone.strip()

        await self.get_lot(norm_villa, norm_lot)
        await self._require_lot_member_or_admin(
            identity, norm_villa, norm_lot, require_anchor=True
        )

        target_mem = await self.repo.get_membership(
            norm_villa, norm_lot, norm_target_phone
        )
        if not target_mem:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El integrante '{norm_target_phone}' no está activo en el lote {norm_lot}.",
            )

        # Un ANCHOR no puede desactivar a otro ANCHOR (solo el ADMIN de la Villa puede hacerlo)
        if (
            target_mem.membership_role == MembershipRole.ANCHOR
            and identity.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el Administrador de la Villa puede dar de baja a una Persona-Ancla (ANCHOR).",
            )

        deactivated = await self.repo.deactivate_membership(
            norm_villa, norm_lot, norm_target_phone
        )
        assert deactivated is not None

        log_audit_event(
            event="LOT_MEMBER_DEACTIVATED",
            actor=f"{norm_villa}:{identity.username}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": norm_villa,
                "lot_id": norm_lot,
                "deactivated_phone": norm_target_phone,
                "deactivated_name": deactivated.full_name,
            },
        )
        return deactivated
