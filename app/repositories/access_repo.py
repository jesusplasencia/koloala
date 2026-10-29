from typing import Protocol

from app.models.access import GuestPass
from app.models.lot import PropertyLot, normalize_lot_id
from app.repositories.lots_repo import in_memory_lot_repo


class AccessRepositoryProtocol(Protocol):
    async def get_lot(self, lot_id: str) -> PropertyLot | None: ...
    async def save_pass(self, guest_pass: GuestPass) -> GuestPass: ...
    async def get_pass_by_code(self, code: str) -> GuestPass | None: ...


class InMemoryAccessRepository:
    def __init__(self, lots_store: dict[str, PropertyLot] | None = None) -> None:
        # Comparte el mismo diccionario de lotes con InMemoryLotRepository (Single Source of Truth)
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = in_memory_lot_repo.lots_store
        self._passes: dict[str, GuestPass] = {}

    async def get_lot(self, lot_id: str) -> PropertyLot | None:
        return self._lots.get(normalize_lot_id(lot_id))

    async def save_pass(self, guest_pass: GuestPass) -> GuestPass:
        self._passes[guest_pass.code.upper()] = guest_pass
        return guest_pass

    async def get_pass_by_code(self, code: str) -> GuestPass | None:
        return self._passes.get(code.upper())


# Instancia singleton en memoria mientras no usemos PostgreSQL
in_memory_repo = InMemoryAccessRepository(lots_store=in_memory_lot_repo.lots_store)
