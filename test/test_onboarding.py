"""Primer ingreso: confirmación de DNI antes de enviar el PIN por WhatsApp (anti-spam)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import auth as auth_module
from app.api.v1.auth import get_lot_repo, get_user_repo
from app.api.v1.lots import get_lots_service
from app.core.security import create_access_token
from app.main import app
from app.models.user import MembershipRole, UserRole
from app.repositories.lots_repo import InMemoryLotRepository
from app.repositories.users_repo import InMemoryUserRepository
from app.services.lots_service import LotsService

API = "/api/v1"
ADMIN = {"Authorization": f"Bearer {create_access_token(username='934253226', role=UserRole.ADMIN)}"}
ANCHOR_B14 = {
    "Authorization": "Bearer "
    + create_access_token(username="916220518", role=UserRole.RESIDENT, lot_id="B14", membership_role=MembershipRole.ANCHOR)
}


@pytest.fixture
def repos():
    return InMemoryLotRepository(), InMemoryUserRepository()


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_send(*, phone, villa_id, lot_id, owner_name, pin_password):
        calls.append(phone)
        return True, "mock"

    monkeypatch.setattr(auth_module, "send_whatsapp_pin_notification", fake_send)
    return calls


@pytest.fixture
async def client(repos):
    lot_repo, user_repo = repos
    app.dependency_overrides[get_lot_repo] = lambda: lot_repo
    app.dependency_overrides[get_user_repo] = lambda: user_repo
    app.dependency_overrides[get_lots_service] = lambda: LotsService(repo=lot_repo, user_repo=user_repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://vc3.lasvillas.club") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _member(lot_repo, phone: str, doc: str | None):
    await lot_repo.upsert_membership(
        villa_id="VC3", lot_id="B14", phone=phone, full_name="Vecina Prueba",
        membership_role=MembershipRole.HOUSEHOLD, document_id=doc,
    )


async def test_member_without_dni_on_file_cannot_activate(client, repos, sent):
    await _member(repos[0], "955000111", None)
    r = await client.post(f"{API}/auth/activate-pin", json={"username": "955000111", "document_id": "12345678"})
    assert r.status_code == 409 and "DNI registrado" in r.json()["detail"]
    assert sent == []


async def test_daily_pin_limit_per_phone(client, repos, sent):
    await _member(repos[0], "955000222", "12345678")
    body = {"username": "955000222", "document_id": "12345678"}
    codes = [(await client.post(f"{API}/auth/activate-pin", json=body)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(sent) == 3


async def test_wrong_dni_three_times_blocks_ip(client, repos, sent):
    await _member(repos[0], "955000333", "12345678")
    body = {"username": "955000333", "document_id": "87654321"}
    codes = [(await client.post(f"{API}/auth/activate-pin", json=body)).status_code for _ in range(3)]
    assert codes == [400, 400, 403]
    ok_after = await client.post(f"{API}/auth/activate-pin", json={"username": "955000333", "document_id": "12345678"})
    assert ok_after.status_code == 403  # IP bloqueada aunque ahora acierte
    assert sent == []


async def test_invalid_document_format_and_ce_accepted(client, repos, sent):
    await _member(repos[0], "955000444", "CE00123456")
    bad = await client.post(f"{API}/auth/activate-pin", json={"username": "955000444", "document_id": "12-34"})
    assert bad.status_code == 422
    ok = await client.post(f"{API}/auth/activate-pin", json={"username": "955000444", "document_id": "ce 00123456"})
    assert ok.status_code == 200


async def test_anchor_must_provide_household_dni_and_admin_can_fix_it(client, repos, sent):
    no_doc = await client.post(f"{API}/lots/B14/members", headers=ANCHOR_B14, json={"phone": "955000555", "full_name": "Sin Dni"})
    assert no_doc.status_code == 422
    ok = await client.post(
        f"{API}/lots/B14/members", headers=ANCHOR_B14,
        json={"phone": "955000555", "full_name": "Con Dni", "document_id": "4011-1222"},
    )
    assert ok.status_code == 201 and ok.json()["document_id"] == "******22"

    # El admin carga a alguien sin DNI y luego lo corrige con PATCH
    await client.post(f"{API}/lots/B14/members", headers=ADMIN, json={"phone": "955000666", "full_name": "Pendiente"})
    pending = await client.post(f"{API}/auth/activate-pin", json={"username": "955000666", "document_id": "40999888"})
    assert pending.status_code == 409
    anchor_try = await client.patch(f"{API}/lots/B14/members/955000666", headers=ANCHOR_B14, json={"document_id": "40999888"})
    assert anchor_try.status_code == 403
    fixed = await client.patch(f"{API}/lots/B14/members/955000666", headers=ADMIN, json={"document_id": "40999888"})
    assert fixed.status_code == 200 and fixed.json()["document_id"] == "******88"
    activated = await client.post(f"{API}/auth/activate-pin", json={"username": "955000666", "document_id": "40999888"})
    assert activated.status_code == 200


async def test_bulk_csv_stores_owner_dni(client, repos, sent):
    r = await client.put(
        f"{API}/lots/bulk", headers=ADMIN,
        json={"lots": [{"lot_id": "Q09", "owner_name": "Familia Ruiz", "owner_phone": "955000777",
                        "owner_document_id": "40777888", "is_maintenance_up_to_date": True}]},
    )
    assert r.status_code == 200
    lookup = await client.post(f"{API}/auth/lookup", json={"username": "955000777"})
    assert lookup.json() == {
        "villa_id": "VC3", "username": "955000777", "status": "NEEDS_DOCUMENT",
        "message": lookup.json()["message"],
    }
    act = await client.post(f"{API}/auth/activate-pin", json={"username": "955000777", "document_id": "40777888"})
    assert act.status_code == 200


async def test_admin_dni_change_resets_access_and_closes_sessions(client, repos, sent, monkeypatch):
    """Un vecino pide por WhatsApp corregir su DNI: el admin lo cambia y su acceso se restablece."""
    lot_repo, user_repo = repos
    await _member(lot_repo, "955000888", "40100100")
    pins: list[str] = []

    async def capture(*, phone, villa_id, lot_id, owner_name, pin_password):
        pins.append(pin_password)
        return True, "mock"

    monkeypatch.setattr(auth_module, "send_whatsapp_pin_notification", capture)
    body = {"username": "955000888", "document_id": "40100100"}
    for _ in range(3):  # agota su límite diario
        assert (await client.post(f"{API}/auth/activate-pin", json=body)).status_code == 200
    login = await client.post(f"{API}/auth/login", json={"username": "955000888", "password": pins[-1]})
    assert login.status_code == 200
    old_token = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get(f"{API}/lots/my-memberships", headers=old_token)).status_code == 200

    members = await client.get(f"{API}/lots/B14/members", headers=ADMIN)
    assert "40100100" not in members.text  # nunca se expone el DNI completo

    changed = await client.patch(f"{API}/lots/B14/members/955000888", headers=ADMIN, json={"document_id": "40200200"})
    assert changed.status_code == 200 and changed.json()["document_id"] == "******00"

    # Sesión abierta cerrada, contraseña anterior inválida y vuelve a "primer ingreso"
    assert (await client.get(f"{API}/lots/my-memberships", headers=old_token)).status_code == 401
    assert (await client.post(f"{API}/auth/login", json={"username": "955000888", "password": pins[-1]})).status_code == 401
    lookup = await client.post(f"{API}/auth/lookup", json={"username": "955000888"})
    assert lookup.json()["status"] == "NEEDS_DOCUMENT"

    # El DNI viejo ya no sirve; el nuevo sí, y el límite diario se reinició
    assert (await client.post(f"{API}/auth/activate-pin", json=body)).status_code == 400
    again = await client.post(f"{API}/auth/activate-pin", json={"username": "955000888", "document_id": "40200200"})
    assert again.status_code == 200
    relogin = await client.post(f"{API}/auth/login", json={"username": "955000888", "password": pins[-1]})
    assert relogin.status_code == 200


async def test_only_admin_can_change_dni(client, repos, sent):
    await _member(repos[0], "955000999", "40300300")
    r = await client.patch(f"{API}/lots/B14/members/955000999", headers=ANCHOR_B14, json={"document_id": "40400400"})
    assert r.status_code == 403
