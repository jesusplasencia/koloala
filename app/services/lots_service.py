"""Padrón de lotes y membresías persona<->lote (ANCHOR / HOUSEHOLD). Nombre y DNI viven en people."""

import math

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.config import settings
from app.core.pii import mask_doc, normalize_document_id
from app.core.security import AuthenticatedIdentity
from app.models.lot import (
    AddHouseholdMemberRequest,
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    LotMembership,
    PaginatedLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
    UpdateMemberRequest,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.models.user import MembershipRole, UserRole
from app.repositories.lots_repo import LotRepositoryProtocol

ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


def _masked(mem: LotMembership) -> LotMembership:
    """El DNI completo nunca sale al navegador: solo ******NN."""
    return mem.model_copy(update={"document_id": mask_doc(None, mem.document_id[-2:]) if mem.document_id else None})


def _clean_doc(raw: str | None) -> str | None:
    try:
        return normalize_document_id(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


def _clean_phone(raw: str) -> str:
    phone = normalize_phone_username(raw) or ""
    if not (phone.isdigit() and len(phone) == 9):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El número de teléfono debe tener 9 dígitos válidos (ej. 980180219).",
        )
    return phone


class LotsService:
    def __init__(self, repo: LotRepositoryProtocol) -> None:
        self.repo = repo
        self.people = repo.people

    # ---------------- lotes ----------------
    async def list_lots(
        self, villa_id: str, page: int = 0, limit: int = 50, is_maintenance_up_to_date: bool | None = None
    ) -> PaginatedLotsResponse:
        norm = normalize_villa_id(villa_id)
        items, total = await self.repo.list_lots(norm, page, limit, is_maintenance_up_to_date)
        total_pages = math.ceil(total / limit) if total > 0 else 0
        return PaginatedLotsResponse(
            villa_id=norm,
            items=items,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
            has_next=(page + 1) < total_pages,
        )

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot:
        norm, norm_id = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        lot = await self.repo.get_lot(norm, norm_id)
        if not lot:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El lote '{norm_id}' no existe en el padrón de {norm}.")
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
        norm, norm_id = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        updated = await self.repo.update_lot_status(norm, norm_id, payload.is_maintenance_up_to_date)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El lote '{norm_id}' no existe en el padrón de {norm}.")
        if payload.owner_name and updated.owner_phone:
            # El nombre del titular vive en people: se corrige en un solo lugar
            owner = await self.people.get_by_phone(updated.owner_phone)
            if owner:
                await self.people.update_person(owner.id, full_name=payload.owner_name.strip())
                updated = await self.get_lot(norm, norm_id)
        log_audit_event(
            event="LOT_MAINTENANCE_UPDATED",
            actor=f"{norm}:{actor}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": norm, "lot_id": updated.lot_id, "is_maintenance_up_to_date": updated.is_maintenance_up_to_date},
        )
        return updated

    async def bulk_upsert_lots(
        self,
        villa_id: str,
        payload: BulkUpsertLotsRequest,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> BulkUpsertLotsResponse:
        """Crea/actualiza lotes y, si traen celular, la PERSONA titular (nombre, DNI) y su membresía ANCHOR."""
        norm = normalize_villa_id(villa_id)
        for item in payload.lots:
            if item.owner_phone:
                _clean_phone(item.owner_phone)
        created, updated, processed = await self.repo.upsert_bulk_lots(norm, payload.lots)
        log_audit_event(
            event="LOTS_BULK_SYNC",
            actor=f"{norm}:{actor}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": norm, "processed_count": len(processed), "created_count": created, "updated_count": updated},
        )
        return BulkUpsertLotsResponse(
            villa_id=norm, processed_count=len(processed), created_count=created, updated_count=updated, lots=processed
        )

    # ---------------- membresías ----------------
    async def list_my_memberships(self, identity: AuthenticatedIdentity) -> list[LotMembership]:
        return [_masked(m) for m in await self.repo.list_memberships_by_person(identity.villa_id, identity.person_id or "")]

    async def _require_lot_member_or_admin(
        self, identity: AuthenticatedIdentity, villa_id: str, lot_id: str, require_anchor: bool = False
    ) -> LotMembership | None:
        if identity.role in ADMIN_ROLES:
            return None
        mem = await self.repo.get_membership(villa_id, lot_id, identity.username)
        if not mem:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tienes permisos ni membresía activa sobre el lote {lot_id} en {villa_id}.",
            )
        if require_anchor and mem.membership_role != MembershipRole.ANCHOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acción restringida: tu rol en el lote {lot_id} es HOUSEHOLD (Conviviente). "
                    "Solo la Persona-Ancla (ANCHOR) o el Administrador pueden agregar o retirar integrantes."
                ),
            )
        return mem

    async def list_lot_members(self, villa_id: str, lot_id: str, identity: AuthenticatedIdentity) -> list[LotMembership]:
        norm, norm_lot = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        await self.get_lot(norm, norm_lot)
        await self._require_lot_member_or_admin(identity, norm, norm_lot)
        return [_masked(m) for m in await self.repo.list_lot_members(norm, norm_lot)]

    async def add_lot_member(
        self,
        villa_id: str,
        lot_id: str,
        payload: AddHouseholdMemberRequest,
        identity: AuthenticatedIdentity,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> LotMembership:
        norm, norm_lot = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        await self.get_lot(norm, norm_lot)
        await self._require_lot_member_or_admin(identity, norm, norm_lot, require_anchor=True)
        phone = _clean_phone(payload.phone)
        is_admin = identity.role in ADMIN_ROLES

        target_role = payload.membership_role if is_admin else MembershipRole.HOUSEHOLD
        if not is_admin:
            existing = await self.repo.get_membership(norm, norm_lot, phone)
            if existing and existing.membership_role == MembershipRole.ANCHOR:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Esta persona ya es Persona-Ancla (ANCHOR) del lote; solo el Administrador puede cambiar su rol.",
                )
            if not existing:
                active_household = sum(
                    1 for m in await self.repo.list_lot_members(norm, norm_lot) if m.membership_role == MembershipRole.HOUSEHOLD
                )
                if active_household >= settings.max_household_per_lot:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"El lote {norm_lot} ya tiene {active_household} convivientes activos "
                            f"(máximo {settings.max_household_per_lot}). Da de baja a alguien o pide ayuda al Administrador."
                        ),
                    )

        doc = _clean_doc(payload.document_id)
        if not doc and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="El DNI o Carnet de Extranjería del integrante es obligatorio: se le pedirá al activar su cuenta.",
            )
        # La persona puede ya existir (p. ej. es ANCHOR de otro lote): un ANCHOR solo completa datos vacíos,
        # nunca reemplaza el nombre o DNI de alguien ya registrado. El Admin sí puede corregirlos.
        person = await self.people.upsert_person(phone, payload.full_name.strip(), doc, overwrite=is_admin)
        if doc and person.document_id and person.document_id != doc and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este celular ya está registrado con otro DNI. Pide al Administrador que lo revise.",
            )
        membership = await self.repo.upsert_membership(
            norm, norm_lot, person.id, target_role, added_by_person_id=identity.person_id, is_active=True
        )
        log_audit_event(
            event="LOT_MEMBER_ADDED",
            actor=f"{norm}:{identity.username}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": norm, "lot_id": norm_lot, "member_phone": phone, "membership_role": str(target_role)},
        )
        return _masked(membership)

    async def deactivate_lot_member(
        self,
        villa_id: str,
        lot_id: str,
        member_phone: str,
        identity: AuthenticatedIdentity,
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> LotMembership:
        norm, norm_lot = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        await self.get_lot(norm, norm_lot)
        await self._require_lot_member_or_admin(identity, norm, norm_lot, require_anchor=True)
        target = await self.repo.get_membership(norm, norm_lot, member_phone)
        if not target or not target.person_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El integrante no está activo en el lote {norm_lot}.")
        if target.membership_role == MembershipRole.ANCHOR and identity.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el Administrador de la Villa puede dar de baja a una Persona-Ancla (ANCHOR).",
            )
        deactivated = await self.repo.deactivate_membership(norm, norm_lot, target.person_id)
        assert deactivated is not None
        log_audit_event(
            event="LOT_MEMBER_DEACTIVATED",
            actor=f"{norm}:{identity.username}",
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": norm, "lot_id": norm_lot, "deactivated_phone": target.phone},
        )
        return _masked(deactivated)

    async def update_lot_member(
        self,
        villa_id: str,
        lot_id: str,
        member_phone: str,
        payload: UpdateMemberRequest,
        identity: AuthenticatedIdentity,
    ) -> LotMembership:
        """
        [SOLO ADMIN] Corrige nombre o DNI de una persona (p. ej. lo pidió por WhatsApp).
        El DNI vive en people: un cambio de DNI RESTABLECE su acceso (contraseña invalidada,
        sesiones cerradas, límite diario de PIN reiniciado). Vuelve a entrar con celular + DNI nuevo.
        """
        if identity.role not in ADMIN_ROLES:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el Administrador puede corregir datos.")
        norm, norm_lot = normalize_villa_id(villa_id), normalize_lot_id(lot_id)
        current = await self.repo.get_membership(norm, norm_lot, member_phone)
        if not current or not current.person_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El integrante no está activo en ese lote.")
        doc = _clean_doc(payload.document_id) if payload.document_id else None
        # Solo se restablece el acceso al REEMPLAZAR un DNI existente; completar uno vacío no bloquea a nadie.
        doc_changed = bool(doc) and bool(current.document_id) and doc != current.document_id
        await self.people.update_person(
            current.person_id,
            full_name=payload.full_name.strip() if payload.full_name else None,
            document_id=doc,
        )
        access_reset = False
        if doc_changed:
            person = await self.people.get_by_id(current.person_id)
            if person and person.password_hash:
                await self.people.reset_access(current.person_id)
                access_reset = True
        log_audit_event(
            event="LOT_MEMBER_DNI_CHANGED_ACCESS_RESET" if doc_changed else "LOT_MEMBER_UPDATED",
            actor=f"{norm}:{identity.username}",
            client_ip="internal",
            user_agent="api",
            details={"villa_id": norm, "lot_id": norm_lot, "member": current.phone, "document_changed": doc_changed, "access_reset": access_reset},
        )
        updated = await self.repo.get_membership(norm, norm_lot, current.phone)
        assert updated is not None
        return _masked(updated)
