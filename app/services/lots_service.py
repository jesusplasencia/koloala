import math

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.security import generate_initial_lot_password
from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    GeneratedLotCredential,
    PaginatedLotsResponse,
    PropertyLot,
    UpdateLotMaintenanceRequest,
    normalize_lot_id,
    normalize_villa_id,
)
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
            initial_password = generate_initial_lot_password(norm_villa, new_lot.lot_id)
            _, created_user = await self.user_repo.create_or_get_resident_user(
                villa_id=norm_villa,
                lot_id=new_lot.lot_id,
                initial_password=initial_password,
                email=new_lot.owner_email,
            )
            if created_user:
                generated_credentials.append(
                    GeneratedLotCredential(
                        villa_id=norm_villa,
                        lot_id=new_lot.lot_id,
                        username=new_lot.lot_id,
                        owner_email=new_lot.owner_email,
                        initial_password=initial_password,
                        login_url=login_url,
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
