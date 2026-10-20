from typing import Protocol

from app.models.access import GuestPass, PropertyLot


class AccessRepositoryProtocol(Protocol):
    async def get_lot(self, lot_id: str) -> PropertyLot | None: ...
    async def save_pass(self, guest_pass: GuestPass) -> GuestPass: ...
    async def get_pass_by_code(self, code: str) -> GuestPass | None: ...


class InMemoryAccessRepository:
    def __init__(self) -> None:
        # Datos semilla simulando el padrón de garita de Villa Club 3
        self._lots: dict[str, PropertyLot] = {
            "MZ-B-L14": PropertyLot(
                lot_id="MZ-B-L14",
                owner_name="Jesus Plasencia",
                is_maintenance_up_to_date=True,
            ),
            "MZ-F-L08": PropertyLot(
                lot_id="MZ-F-L08",
                owner_name="Vecino Moroso",
                is_maintenance_up_to_date=False,
            ),
        }
        self._passes: dict[str, GuestPass] = {}

    async def get_lot(self, lot_id: str) -> PropertyLot | None:
        return self._lots.get(lot_id.upper())

    async def save_pass(self, guest_pass: GuestPass) -> GuestPass:
        self._passes[guest_pass.code.upper()] = guest_pass
        return guest_pass

    async def get_pass_by_code(self, code: str) -> GuestPass | None:
        return self._passes.get(code.upper())


# Instancia singleton en memoria mientras no usemos PostgreSQL
in_memory_repo = InMemoryAccessRepository()
