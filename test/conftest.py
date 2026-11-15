"""Fixtures compartidas: un 'mundo' en memoria (personas, lotes, visitas) por test, nunca Supabase."""

from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import auth as auth_module
from app.api.v1.auth import get_lot_repo
from app.api.v1.lots import get_lots_service
from app.api.v1.visits import get_visit_service
from app.core.config import settings
from app.core.security import create_access_token, get_people_repo
from app.main import app
from app.models.user import MembershipRole, UserRole
from app.repositories.lots_repo import InMemoryLotRepository
from app.repositories.people_repo import InMemoryPeopleRepository
from app.repositories.visit_repo import InMemoryVisitRepository
from app.services.lots_service import LotsService
from app.services.visit_service import VisitService

API = "/api/v1"

# Personas semilla del repositorio en memoria (ver InMemoryPeopleRepository._seed)
JESUS = "916220518"  # SUPER_ADMIN, ANCHOR de VC3-B14
ADMIN = "934253226"  # ADMIN de VC3
GUARD = "900000001"  # GUARD de VC3 (ficticio)
GUARD_VC2 = "900000002"  # GUARD de VC2 (ficticio)
MOROSO = "999111222"  # ANCHOR de VC3-F08 (en mora)
VC2_OWNER = "988777666"  # ANCHOR de VC2-B14


@pytest.fixture(autouse=True)
def _offline_repositories(monkeypatch):
    """Los tests siempre usan repositorios en memoria, aunque el .env apunte a Supabase."""
    monkeypatch.setattr(settings, "use_postgres", False)


@dataclass
class World:
    people: InMemoryPeopleRepository
    lots: InMemoryLotRepository
    visits: InMemoryVisitRepository

    def headers(
        self,
        phone: str,
        role: UserRole,
        villa: str = "VC3",
        lot: str | None = None,
        mem: MembershipRole | None = None,
        name: str | None = None,
    ) -> dict[str, str]:
        """Token firmado para una persona (la crea si no existe). El JWT lleva su UUID, no el celular."""
        person = self.people.ensure_person(phone, name)
        token = create_access_token(
            person_id=person.id,
            role=role,
            villa_id=villa,
            lot_id=lot,
            membership_role=mem,
            full_name=person.full_name,
            token_version=person.token_version,
        )
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world() -> World:
    people = InMemoryPeopleRepository()
    lots = InMemoryLotRepository(people)
    visits = InMemoryVisitRepository(lots)
    w = World(people=people, lots=lots, visits=visits)
    app.dependency_overrides[get_people_repo] = lambda: people
    app.dependency_overrides[get_lot_repo] = lambda: lots
    app.dependency_overrides[get_lots_service] = lambda: LotsService(repo=lots)
    app.dependency_overrides[get_visit_service] = lambda: VisitService(visits)
    yield w
    app.dependency_overrides.clear()


@pytest.fixture
async def client(world):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://vc3.lasvillas.club") as ac:
        yield ac


@pytest.fixture
def pins(monkeypatch) -> dict[str, str]:
    """Captura los PIN que se 'envían' por WhatsApp: {celular_51...: pin}."""
    captured: dict[str, str] = {}

    async def fake_send(*, phone, villa_id, lot_id, owner_name, pin_password):
        captured[phone] = pin_password
        return True, "mock"

    monkeypatch.setattr(auth_module, "send_whatsapp_pin_notification", fake_send)
    return captured


@pytest.fixture
def h(world):
    """Atajos de cabeceras por rol."""

    class _H:
        jesus = world.headers(JESUS, UserRole.SUPER_ADMIN, lot="B14", mem=MembershipRole.ANCHOR)
        anchor_b14 = world.headers(JESUS, UserRole.RESIDENT, lot="B14", mem=MembershipRole.ANCHOR)
        admin = world.headers(ADMIN, UserRole.ADMIN)
        guard = world.headers(GUARD, UserRole.GUARD)
        guard_vc2 = world.headers(GUARD_VC2, UserRole.GUARD, villa="VC2")
        moroso = world.headers(MOROSO, UserRole.RESIDENT, lot="F08", mem=MembershipRole.ANCHOR)

    return _H
