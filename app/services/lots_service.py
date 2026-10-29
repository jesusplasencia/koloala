from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.security import generate_initial_lot_password
from app.models.lot import (
    BulkUpsertLotsRequest,
    BulkUpsertLotsResponse,
    GeneratedLotCredential,
    PropertyLot,
    UpdateLotMaintenanceRequest,
    normalize_lot_id,
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

    async def list_lots(self) -> list[PropertyLot]:
        return await self.repo.list_lots()

    async def get_lot(self, lot_id: str) -> PropertyLot:
        norm_id = normalize_lot_id(lot_id)
        lot = await self.repo.get_lot(norm_id)
        if not lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{norm_id}' no existe en el padrón de Villa Club 3.",
            )
        return lot

    async def update_lot_maintenance(
        self,
        lot_id: str,
        payload: UpdateLotMaintenanceRequest,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> PropertyLot:
        norm_id = normalize_lot_id(lot_id)
        updated_lot = await self.repo.update_lot_status(
            lot_id=norm_id,
            is_maintenance_up_to_date=payload.is_maintenance_up_to_date,
            owner_name=payload.owner_name,
        )
        if not updated_lot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El lote '{norm_id}' no existe en el padrón de Villa Club 3.",
            )

        log_audit_event(
            event="LOT_MAINTENANCE_UPDATED",
            actor=actor,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "lot_id": updated_lot.lot_id,
                "is_maintenance_up_to_date": updated_lot.is_maintenance_up_to_date,
                "owner_name": updated_lot.owner_name,
            },
        )
        return updated_lot

    async def bulk_upsert_lots(
        self,
        payload: BulkUpsertLotsRequest,
        actor: str = "ADMIN",
        client_ip: str = "internal",
        user_agent: str = "unknown",
    ) -> BulkUpsertLotsResponse:
        created_count, updated_count, processed_lots, new_lot_ids = (
            await self.repo.upsert_bulk_lots(payload.lots)
        )

        generated_credentials: list[GeneratedLotCredential] = []
        for new_lot_id in new_lot_ids:
            initial_password = generate_initial_lot_password(new_lot_id)
            _, created_user = await self.user_repo.create_or_get_resident_user(
                lot_id=new_lot_id,
                initial_password=initial_password,
            )
            if created_user:
                generated_credentials.append(
                    GeneratedLotCredential(
                        lot_id=new_lot_id,
                        username=new_lot_id,
                        initial_password=initial_password,
                    )
                )

        log_audit_event(
            event="LOTS_BULK_SYNC",
            actor=actor,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "processed_count": len(processed_lots),
                "created_count": created_count,
                "updated_count": updated_count,
                "new_lot_ids": new_lot_ids,
            },
        )

        return BulkUpsertLotsResponse(
            processed_count=len(processed_lots),
            created_count=created_count,
            updated_count=updated_count,
            lots=processed_lots,
            generated_credentials=generated_credentials,
        )
