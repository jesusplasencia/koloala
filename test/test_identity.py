"""Identidad ordenada: people (UUID) + staff_roles + lot_memberships. Login, padrón, personal y seguridad."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.user import AttackType, MembershipRole, UserRole
from test.conftest import ADMIN, API, GUARD, JESUS, MOROSO, VC2_OWNER


async def _login(client, phone, password, villa=None, ip=None):
    headers = {}
    if villa:
        headers["X-Villa-Id"] = villa
    if ip:
        headers["X-Forwarded-For"] = ip
    return await client.post(f"{API}/auth/login", headers=headers, json={"username": phone, "password": password})


async def _activate(client, phone, doc, villa=None):
    headers = {"X-Villa-Id": villa} if villa else {}
    return await client.post(f"{API}/auth/activate-pin", headers=headers, json={"username": phone, "document_id": doc})


# ---------------------------------------------------------------------------
# Login por celular para los 3 roles; un admin dueño de lote es UNA sola persona
# ---------------------------------------------------------------------------
async def test_three_roles_login_by_phone(client):
    jesus = await _login(client, JESUS, "91622051")
    assert jesus.status_code == 200, jesus.text
    data = jesus.json()
    assert data["role"] == "SUPER_ADMIN" and data["lot_id"] == "B14" and data["available_lots"] == ["B14"]
    assert data["username"] == JESUS and data["full_name"] == "Jesus Plasencia"

    admin = await _login(client, ADMIN, "93425322")
    assert admin.json()["role"] == "ADMIN" and admin.json()["lot_id"] is None
    guard = await _login(client, GUARD, "90000001")
    assert guard.json()["role"] == "GUARD"

    # El JWT lleva el UUID de la persona, no su celular
    import base64
    import json

    payload_b64 = data["access_token"].split(".")[1]
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert payload["sub"] != JESUS and len(payload["sub"]) == 36

    me = await client.get(f"{API}/lots/my-memberships", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert [m["lot_id"] for m in me.json()] == ["B14"]


async def test_lot_codes_and_system_usernames_no_longer_exist(client):
    assert (await client.post(f"{API}/auth/lookup", json={"username": "B14"})).status_code == 422
    assert (await client.post(f"{API}/auth/login", json={"username": "GUARDIA1", "password": "guardia123"})).status_code == 422
    assert (await client.post(f"{API}/auth/login", json={"username": "B14", "password": "VC3-B14"})).status_code == 422
    assert (await client.post(f"{API}/lots/B14/reset-password", json={})).status_code in (404, 405)


async def test_unknown_number_gets_no_account(client):
    r = await client.post(f"{API}/auth/lookup", json={"username": "955443322"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "UNREGISTERED_NO_ACCOUNT" and "QR" in body["message"]
    assert set(body) == {"villa_id", "username", "status", "message"}
    assert (await client.post(f"{API}/auth/register-visitor", json={})).status_code == 404
    assert (await client.post(f"{API}/passes", json={})).status_code == 404


# ---------------------------------------------------------------------------
# Padrón: crea la PERSONA titular (nombre + DNI) y su membresía; activa con DNI
# ---------------------------------------------------------------------------
async def test_bulk_creates_owner_person_and_dni_activation(client, h, pins):
    r = await client.put(
        f"{API}/lots/bulk",
        headers=h.admin,
        json={"lots": [{"lot_id": "P21", "owner_name": "Familia Vargas", "owner_phone": "987 654 321",
                        "owner_document_id": "45891230", "is_maintenance_up_to_date": True}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created_count"] == 1
    assert r.json()["lots"][0]["owner_name"] == "Familia Vargas"
    assert "generated_credentials" not in r.json()

    lookup = await client.post(f"{API}/auth/lookup", json={"username": "987654321"})
    assert lookup.json()["status"] == "NEEDS_DOCUMENT" and "Vargas" not in lookup.text

    assert (await _activate(client, "987654321", "11111111")).status_code == 400
    assert pins == {}
    ok = await _activate(client, "987654321", "45891230")
    assert ok.status_code == 200 and ok.json()["lot_id"] == "P21"
    login = await _login(client, "987654321", pins["51987654321"])
    assert login.status_code == 200
    assert login.json()["role"] == "RESIDENT" and login.json()["lot_id"] == "P21"
    assert (await client.post(f"{API}/auth/lookup", json={"username": "987654321"})).json()["status"] == "READY_FOR_PASSWORD"
    assert (await _activate(client, "987654321", "45891230")).status_code == 409


async def test_name_lives_in_one_place(client, h):
    await client.put(
        f"{API}/lots/bulk",
        headers=h.admin,
        json={"lots": [
            {"lot_id": "Q02", "owner_name": "Pepito Juarez", "owner_phone": "911222333", "owner_document_id": "40111222", "is_maintenance_up_to_date": True},
            {"lot_id": "Q03", "owner_name": "Pepito Juarez", "owner_phone": "911222333", "is_maintenance_up_to_date": True},
        ]},
    )
    renamed = await client.patch(
        f"{API}/lots/Q02/maintenance", headers=h.admin,
        json={"is_maintenance_up_to_date": True, "owner_name": "José Juárez"},
    )
    assert renamed.status_code == 200 and renamed.json()["owner_name"] == "José Juárez"
    other = await client.get(f"{API}/lots/Q03", headers=h.admin)
    assert other.json()["owner_name"] == "José Juárez"  # misma persona, un solo registro


async def test_lots_pagination_and_filtering(client, h):
    await client.put(
        f"{API}/lots/bulk",
        headers=h.admin,
        json={"lots": [
            {"lot_id": "A01", "is_maintenance_up_to_date": True},
            {"lot_id": "P20", "is_maintenance_up_to_date": True},
            {"lot_id": "U28", "is_maintenance_up_to_date": False},
        ]},
    )
    body = (await client.get(f"{API}/lots", headers=h.admin)).json()
    assert body["total"] == 5 and [i["lot_id"] for i in body["items"]] == ["A01", "B14", "F08", "P20", "U28"]
    assert [i["owner_name"] for i in body["items"]][:3] == [None, "Jesus Plasencia", "Vecino Moroso"]
    page2 = (await client.get(f"{API}/lots?page=2&limit=2", headers=h.admin)).json()
    assert [x["lot_id"] for x in page2["items"]] == ["U28"] and page2["has_next"] is False
    delinquents = (await client.get(f"{API}/lots?is_maintenance_up_to_date=false", headers=h.admin)).json()
    assert [x["lot_id"] for x in delinquents["items"]] == ["F08", "U28"]


# ---------------------------------------------------------------------------
# ANCHOR / HOUSEHOLD
# ---------------------------------------------------------------------------
async def test_anchor_adds_household_who_activates_and_is_removed(client, h, world, pins):
    add = await client.post(
        f"{API}/lots/B14/members", headers=h.anchor_b14,
        json={"phone": "980180219", "full_name": "Angel Plasencia", "document_id": "71234567"},
    )
    assert add.status_code == 201 and add.json()["membership_role"] == "HOUSEHOLD"
    assert add.json()["added_by"] == JESUS and add.json()["document_id"] == "******67"

    assert (await client.post(f"{API}/auth/lookup", json={"username": "980180219"})).json()["status"] == "NEEDS_DOCUMENT"
    assert (await _activate(client, "980180219", "71234567")).status_code == 200
    login = await _login(client, "980180219", pins["51980180219"])
    assert login.json()["membership_role"] == "HOUSEHOLD" and login.json()["lot_id"] == "B14"
    angel = {"Authorization": f"Bearer {login.json()['access_token']}"}

    invite = await client.post(f"{API}/visits/invite", headers=angel, json={"note": "Amigo Caillou"})
    assert invite.status_code == 201
    view = await client.get(f"{API}/guard/visits/{invite.json()['short_code']}", headers=h.guard)
    assert view.json()["host_name"] == "Angel Plasencia" and view.json()["host_role"] == "HOUSEHOLD"

    forbidden = await client.post(f"{API}/lots/B14/members", headers=angel, json={"phone": "955111222", "full_name": "Intruso", "document_id": "40000099"})
    assert forbidden.status_code == 403
    assert (await client.delete(f"{API}/lots/B14/members/{JESUS}", headers=angel)).status_code == 403

    removed = await client.delete(f"{API}/lots/B14/members/980180219", headers=h.anchor_b14)
    assert removed.status_code == 200 and removed.json()["is_active"] is False
    assert (await client.post(f"{API}/visits/invite", headers=angel, json={})).status_code == 403
    members = await client.get(f"{API}/lots/B14/members", headers=h.anchor_b14)
    assert [m["phone"] for m in members.json()] == [JESUS]

    readd = await client.post(
        f"{API}/lots/B14/members", headers=h.anchor_b14,
        json={"phone": "980180219", "full_name": "Angel Plasencia", "document_id": "71234567"},
    )
    assert readd.status_code == 201
    assert (await client.post(f"{API}/visits/invite", headers=angel, json={})).status_code == 201


async def test_anchor_cannot_overwrite_existing_person_dni(client, h):
    await client.put(
        f"{API}/lots/bulk", headers=h.admin,
        json={"lots": [{"lot_id": "Q09", "owner_name": "Otra Persona", "owner_phone": "944555666", "owner_document_id": "40333444", "is_maintenance_up_to_date": True}]},
    )
    r = await client.post(
        f"{API}/lots/B14/members", headers=h.anchor_b14,
        json={"phone": "944555666", "full_name": "Nombre Falso", "document_id": "40999999"},
    )
    assert r.status_code == 409
    ok = await client.post(
        f"{API}/lots/B14/members", headers=h.anchor_b14,
        json={"phone": "944555666", "full_name": "Nombre Falso", "document_id": "40333444"},
    )
    assert ok.status_code == 201 and ok.json()["full_name"] == "Otra Persona"  # no se pisa el nombre


async def test_multi_lot_person_one_pin_two_villas(client, h, world, pins):
    jesus_vc2 = world.headers(JESUS, UserRole.SUPER_ADMIN)
    await client.put(f"{API}/lots/bulk", headers=h.admin, json={"lots": [
        {"lot_id": "P30", "owner_name": "Pepito MultiVilla", "owner_phone": "977888999", "owner_document_id": "40555666", "is_maintenance_up_to_date": True}]})
    await client.put(f"{API}/lots/bulk", headers={**jesus_vc2, "X-Villa-Id": "VC2"}, json={"lots": [
        {"lot_id": "M20", "owner_name": "Pepito MultiVilla", "owner_phone": "977888999", "is_maintenance_up_to_date": True}]})
    assert (await _activate(client, "977888999", "40555666")).status_code == 200
    pin = pins["51977888999"]
    vc3 = await _login(client, "977888999", pin, villa="VC3")
    vc2 = await _login(client, "977888999", pin, villa="VC2")
    assert vc3.json()["lot_id"] == "P30" and vc2.json()["lot_id"] == "M20"


# ---------------------------------------------------------------------------
# Seguridad y aislamiento
# ---------------------------------------------------------------------------
async def test_password_spraying_blocks_ip_and_points_to_suspect(client, h, world):
    world.people.ensure_person(MOROSO, pin="11112222")
    ip = "190.236.99.88"
    assert (await _login(client, MOROSO, "11112222", ip=ip)).status_code == 200
    assert (await _login(client, JESUS, "xxxx", ip=ip)).status_code == 401
    assert (await _login(client, "955000001", "xxxx", ip=ip)).status_code == 401
    third = await _login(client, ADMIN, "xxxx", ip=ip)
    assert third.status_code == 403 and "PASSWORD_SPRAYING" in third.json()["detail"]
    reports = (await client.get(f"{API}/auth/security/blocked-ips", headers=h.admin)).json()
    assert reports[0]["attack_type"] == AttackType.PASSWORD_SPRAYING
    assert reports[0]["attempted_usernames"] == [JESUS, "955000001", ADMIN]
    assert reports[0]["suspected_lots_from_previous_logins"] == [MOROSO]
    assert (await client.delete(f"{API}/auth/security/blocked-ips/{ip}", headers=h.admin)).status_code == 200
    assert (await _login(client, MOROSO, "11112222", ip=ip)).status_code == 200


async def test_multi_villa_isolation(client, h, world):
    vc3 = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={"kind": "DELIVERY"})
    assert vc3.status_code == 201
    world.people.ensure_person(VC2_OWNER, pin="22223333")
    vc2_login = await _login(client, VC2_OWNER, "22223333", villa="VC2")
    assert vc2_login.status_code == 200 and vc2_login.json()["lot_id"] == "B14"
    vc2_headers = {"Authorization": f"Bearer {vc2_login.json()['access_token']}", "X-Villa-Id": "VC2"}
    assert (await client.post(f"{API}/visits/invite", headers=vc2_headers, json={"kind": "DELIVERY"})).status_code == 403
    cross = await client.post(f"{API}/visits/invite", headers={**h.anchor_b14, "X-Villa-Id": "VC2"}, json={"kind": "TAXI"})
    assert cross.status_code == 403 and "pertenece a VC3" in cross.json()["detail"]
    look = await client.get(f"{API}/guard/visits/{vc3.json()['short_code']}", headers={**h.guard_vc2, "X-Villa-Id": "VC2"})
    assert look.status_code == 404


# ---------------------------------------------------------------------------
# Personal (vigilantes / administradores)
# ---------------------------------------------------------------------------
async def test_staff_management(client, h, world, pins):
    created = await client.post(
        f"{API}/admin/staff", headers=h.admin,
        json={"phone": "955000100", "full_name": "Vigilante Nuevo", "document_id": "40000100", "role": "GUARD"},
    )
    assert created.status_code == 201 and created.json()["has_activated"] is False
    assert created.json()["document_id"] == "******00"
    staff = (await client.get(f"{API}/admin/staff", headers=h.admin)).json()
    assert {s["phone"] for s in staff} >= {ADMIN, GUARD, "955000100"}

    assert (await _activate(client, "955000100", "40000100")).status_code == 200
    login = await _login(client, "955000100", pins["51955000100"])
    assert login.json()["role"] == "GUARD"

    # Un ADMIN no puede nombrar administradores; el SUPER_ADMIN sí
    assert (await client.post(f"{API}/admin/staff", headers=h.admin, json={"phone": "955000200", "full_name": "Otro Admin", "role": "ADMIN"})).status_code == 403
    assert (await client.post(f"{API}/admin/staff", headers=h.jesus, json={"phone": "955000200", "full_name": "Otro Admin", "role": "ADMIN"})).status_code == 201

    gone = await client.delete(f"{API}/admin/staff/955000100", headers=h.admin)
    assert gone.status_code == 200 and gone.json()["is_active"] is False
    guard_token = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get(f"{API}/guard/visits/daily-log", headers=guard_token)).status_code == 401
    assert (await client.post(f"{API}/auth/lookup", json={"username": "955000100"})).json()["status"] == "UNREGISTERED_NO_ACCOUNT"


def test_audit_gz_rotation_cleanup_and_s3_deletion(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import audit
    from app.core.config import settings

    monkeypatch.setattr(settings, "audit_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_local_gz_files", 5)
    monkeypatch.setattr(settings, "s3_audit_bucket", None)
    for i in range(8):
        raw = tmp_path / "audit.jsonl"
        raw.write_text(f'{{"event": "TEST_{i}"}}\n', encoding="utf-8")
        audit._hourly_s3_rotator(str(raw), str(tmp_path / f"audit.jsonl.2026-11-05_{i:02d}"))
    assert not (tmp_path / "audit.jsonl").exists()
    assert len(list(tmp_path.glob("*.gz"))) == 5

    uploaded: list[str] = []

    def fake_upload(gz_path) -> bool:
        uploaded.append(gz_path.name)
        gz_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(settings, "s3_audit_bucket", "lasvillas-audit-bucket")
    monkeypatch.setattr(audit, "_upload_gz_to_s3", fake_upload)
    raw = tmp_path / "audit.jsonl"
    raw.write_text('{"event": "FINAL"}\n', encoding="utf-8")
    audit._hourly_s3_rotator(str(raw), str(tmp_path / "audit.jsonl.2026-11-05_99"))
    assert len(uploaded) == 6 and list(tmp_path.glob("*.gz")) == []


_ = (ASGITransport, AsyncClient, app, MembershipRole)
