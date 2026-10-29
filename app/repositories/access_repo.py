from typing import Protocol

from app.models.access import GuestPass
from app.models.lot import PropertyLot, normalize_lot_id, normalize_villa_id
from app.repositories.lots_repo import in_memory_lot_repo


class AccessRepositoryProtocol(Protocol):
    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None: ...
    async def save_pass(self, guest_pass: GuestPass) -> GuestPass: ...
    async def get_pass_by_code(
        self, villa_id: str, code: str
    ) -> GuestPass | None: ...


class InMemoryAccessRepository:
    def __init__(
        self, lots_store: dict[tuple[str, str], PropertyLot] | None = None
    ) -> None:
        # Comparte el mismo diccionario de lotes (villa_id, lot_id) con InMemoryLotRepository
        if lots_store is not None:
            self._lots = lots_store
        else:
            self._lots = in_memory_lot_repo.lots_store
        # Llave compuesta para los pases: (villa_id, code)
        self._passes: dict[tuple[str, str], GuestPass] = {}

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        key = (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        return self._lots.get(key)

    async def save_pass(self, guest_pass: GuestPass) -> GuestPass:
        key = (normalize_villa_id(guest_pass.villa_id), guest_pass.code.upper())
        self._passes[key] = guest_pass
        return guest_pass

    async def get_pass_by_code(
        self, villa_id: str, code: str
    ) -> GuestPass | None:
        key = (normalize_villa_id(villa_id), code.upper())
        return self._passes.get(key)


# Instancia singleton en memoria mientras no usemos PostgreSQL
in_memory_repo = InMemoryAccessRepository(lots_store=in_memory_lot_repo.lots_store)
