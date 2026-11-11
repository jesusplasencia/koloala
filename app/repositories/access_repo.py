from typing import Protocol

from app.models.access import GuestPass
from app.models.lot import (
    LotMembership,
    PropertyLot,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.repositories.lots_repo import in_memory_lot_repo


class AccessRepositoryProtocol(Protocol):
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None: ...
    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None: ...
    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]: ...
    async def save_pass(self, guest_pass: GuestPass) -> GuestPass: ...
    async def get_pass_by_code(
        self, villa_id: str, code: str
    ) -> GuestPass | None: ...
    async def list_passes_for_villa(
        self, villa_id: str, limit: int = 200
    ) -> list[GuestPass]: ...


class InMemoryAccessRepository:
    def __init__(
        self,
        lots_store: dict[tuple[str, str], PropertyLot] | None = None,
        memberships_store: dict[tuple[str, str, str], LotMembership] | None = None,
    ) -> None:
        # Comparte el mismo diccionario de lotes (villa_id, lot_id) y membresías con InMemoryLotRepository
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = in_memory_lot_repo.lots_store

        if memberships_store is not None:
            self._memberships = memberships_store
        else:
            self._memberships = in_memory_lot_repo.memberships_store

        # Llave compuesta para los pases: (villa_id, code)
        self._passes: dict[tuple[str, str], GuestPass] = {}

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        return self._lots.get(key)

    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        mem = self._memberships.get((norm_villa, norm_lot, norm_phone))
        if not mem or not mem.is_active:
            return None
        return mem

    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        return [
            mem
            for mem in self._memberships.values()
            if mem.villa_id == norm_villa and mem.phone == norm_phone and mem.is_active
        ]

    async def save_pass(self, guest_pass: GuestPass) -> GuestPass:
        key = (normalize_villa_id(guest_pass.villa_id), guest_pass.code.upper())
        self._passes[key] = guest_pass
        return guest_pass

    async def get_pass_by_code(
        self, villa_id: str, code: str
    ) -> GuestPass | None:
        key = (normalize_villa_id(villa_id), code.upper())
        return self._passes.get(key)

    async def list_passes_for_villa(
        self, villa_id: str, limit: int = 200
    ) -> list[GuestPass]:
        norm_villa = normalize_villa_id(villa_id)
        items = [
            p for p in self._passes.values() if p.villa_id == norm_villa
        ]
        items.sort(key=lambda p: p.created_at, reverse=True)
        return items[:limit]


# Instancia singleton en memoria mientras no usemos PostgreSQL
in_memory_repo = InMemoryAccessRepository(
    lots_store=in_memory_lot_repo.lots_store,
    memberships_store=in_memory_lot_repo.memberships_store,
)
