"""Carnet QR del residente, ingresos repetidos, DOC_DIGITAL, tope de HOUSEHOLD y bitácora enmascarada."""

import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.lots import get_lots_service
from app.api.v1.visits import get_visit_service
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.models.lot import LotMembership
from app.models.user import MembershipRole, UserRole
from app.repositories.lots_repo import InMemoryLotRepository
from app.repositories.users_repo import InMemoryUserRepository
from app.repositories.visit_repo import InMemoryVisitRepository
from app.services.entry_service import build_carnet_payload
from app.services.lots_service import LotsService
from app.services.visit_service import PublicRateLimiter, VisitService

API = "/api/v1"


def _h(username, role, villa="VC3", lot=None, mem=None):
    t = create_access_token(username=username, role=role, villa_id=villa, lot_id=lot, membership_role=mem)
    return {"Authorization": f"Bearer {t}"}


JESUS = _h("916220518", UserRole.SUPER_ADMIN, lot="B14", mem=MembershipRole.ANCHOR)
MOROSO = _h("999111222", UserRole.RESIDENT, lot="F08", mem=MembershipRole.ANCHOR)
NOBODY = _h("955555555", UserRole.RESIDENT)
GUARD = _h("GUARDIA1", UserRole.GUARD)
ADMIN = _h("934253226", UserRole.ADMIN)


@pytest.fixture
def lot_repo() -> InMemoryLotRepository:
    return InMemoryLotRepository()


@pytest.fixture
def visit_repo(lot_repo) -> InMemoryVisitRepository:
    return InMemoryVisitRepository(lot_repo.lots_store, lot_repo.memberships_store)


@pytest.fixture
async def client(lot_repo, visit_repo):
    app.dependency_overrides[get_visit_service] = lambda: VisitService(visit_repo, PublicRateLimiter())
    app.dependency_overrides[get_lots_service] = lambda: LotsService(
        repo=lot_repo, user_repo=InMemoryUserRepository()
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://vc3.lasvillas.club") as ac:
        yield ac
    app.dependency_overrides.clear()


def _add_membership(lot_repo, lot, phone, name, role):
    now = datetime.now(UTC)
    lot_repo.memberships_store[("VC3", lot, phone)] = LotMembership(
        villa_id="VC3", lot_id=lot, phone=phone, full_name=name,
        membership_role=role, created_at=now, updated_at=now,
    )


async def test_admin_owner_enters_with_rotating_carnet_many_times(client: AsyncClient, lot_repo):
    # Jesús también es HOUSEHOLD del F08 (lote con deuda): un solo carnet, ve ambos lotes
    _add_membership(lot_repo, "F08", "916220518", "Jesus Plasencia", MembershipRole.HOUSEHOLD)
    carnet = await client.get(f"{API}/me/carnet", headers=JESUS)
    assert carnet.status_code == 200, carnet.text
    c = carnet.json()
    assert c["window_seconds"] == 30 and 0 < c["expires_in"] <= 30
    assert {(lot["lot_id"], lot["membership_role"]) for lot in c["lots"]} == {
        ("B14", "ANCHOR"), ("F08", "HOUSEHOLD")}

    for _ in range(3):  # el carnet no se gasta
        r = await client.post(f"{API}/guard/resident-entry", headers=GUARD,
                              json={"qr_payload": c["payload"], "vehicle_plate": "abc-123"})
        assert r.status_code == 201, r.text
    body = r.json()
    assert body["person_name"] == "Jesus Plasencia" and body["method"] == "QR"
    assert body["message"] == "RESIDENTE: Jesus Plasencia - B14, F08"
    assert body["warnings"] == ["Lote F08 con deuda de mantenimiento"]  # advierte, no bloquea
    assert "916220518" not in r.text  # el guardia no ve el celular

    mine = await client.get(f"{API}/me/entries", headers=JESUS)
    assert len(mine.json()["items"]) == 3
    log = await client.get(f"{API}/guard/visits/daily-log", headers=GUARD)
    assert log.json()["resident_entries"] == 3
    search = await client.get(f"{API}/admin/access-search?plate=ABC123", headers=ADMIN)
    assert len(search.json()["resident_entries"]) == 3


async def test_expired_tampered_and_foreign_villa_carnet(client: AsyncClient):
    old, _ = build_carnet_payload("VC3", "916220518", now_s=time.time() - 3 * settings.resident_qr_window_seconds)
    r = await client.post(f"{API}/guard/resident-entry", headers=GUARD, json={"qr_payload": old})
    assert r.status_code == 409
    good, _ = build_carnet_payload("VC3", "916220518")
    forged = good.replace("916220518", "999111222")
    r = await client.post(f"{API}/guard/resident-entry", headers=GUARD, json={"qr_payload": forged})
    assert r.status_code == 409
    vc2, _ = build_carnet_payload("VC2", "988777666")
    r = await client.post(f"{API}/guard/resident-entry", headers=GUARD, json={"qr_payload": vc2})
    assert r.status_code == 409
    resident_try = await client.post(f"{API}/guard/resident-entry", headers=JESUS, json={"qr_payload": good})
    assert resident_try.status_code == 201  # SUPER_ADMIN también puede operar garita


async def test_no_membership_no_carnet_and_deactivated_loses_access(client: AsyncClient, lot_repo):
    assert (await client.get(f"{API}/me/carnet", headers=NOBODY)).status_code == 403
    _add_membership(lot_repo, "B14", "955555555", "Angel", MembershipRole.HOUSEHOLD)
    payload = (await client.get(f"{API}/me/carnet", headers=NOBODY)).json()["payload"]
    lot_repo.memberships_store[("VC3", "B14", "955555555")].is_active = False
    r = await client.post(f"{API}/guard/resident-entry", headers=GUARD, json={"qr_payload": payload})
    assert r.status_code == 403


async def test_manual_entry_without_phone(client: AsyncClient):
    ok = await client.post(f"{API}/guard/resident-entry", headers=GUARD,
                           json={"phone": "916 220 518", "lot_id": "b14"})
    assert ok.status_code == 201 and ok.json()["method"] == "MANUAL"
    wrong = await client.post(f"{API}/guard/resident-entry", headers=GUARD,
                              json={"phone": "916220518", "lot_id": "F08"})
    assert wrong.status_code == 404


async def test_household_cap_per_lot(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "max_household_per_lot", 2)
    owner = _h("916220518", UserRole.RESIDENT, lot="B14", mem=MembershipRole.ANCHOR)
    codes = []
    for i in range(3):
        r = await client.post(f"{API}/lots/B14/members", headers=owner,
                              json={"phone": f"98000000{i}", "full_name": f"Familiar {i}"})
        codes.append(r.status_code)
    assert codes[:2] == [codes[0], codes[0]] and codes[0] in (200, 201)
    assert codes[2] == 409
    # El Admin sí puede superar el tope
    r = await client.post(f"{API}/lots/B14/members", headers=ADMIN,
                          json={"phone": "980000009", "full_name": "Extra"})
    assert r.status_code in (200, 201)


async def test_doc_digital_and_masked_daily_log(client: AsyncClient, visit_repo):
    r = await client.post(f"{API}/visits/invite", headers=JESUS,
                          json={"attendees": [{"first_names": "Ana", "doc_number": "45678912"}]})
    token = r.json()["token"]
    ci = await client.post(f"{API}/guard/visits/{token}/check-in", headers=GUARD,
                           json={"attendees": [{"attendee_no": 1, "verification": "DOC_DIGITAL"}]})
    assert ci.status_code == 200 and ci.json()["attendees"][0]["verification"] == "DOC_DIGITAL"

    masked = await client.get(f"{API}/guard/visits/daily-log", headers=GUARD)
    assert masked.json()["items"][0]["attendees"][0]["doc_display"] == "******12"
    assert "45678912" not in masked.text and visit_repo.data_access_log == []

    revealed = await client.get(f"{API}/guard/visits/daily-log?reveal=true", headers=GUARD)
    assert revealed.json()["items"][0]["attendees"][0]["doc_display"] == "45678912"
    assert visit_repo.data_access_log[-1]["action"] == "DAILY_LOG_REVEAL"
    assert visit_repo.data_access_log[-1]["actor"] == "GUARDIA1"
