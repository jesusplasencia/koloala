"""Primer ingreso con DNI (anti-spam de PIN) y restablecimiento de acceso por el Administrador."""

from app.models.user import MembershipRole
from test.conftest import API


async def _member(world, phone: str, doc: str | None):
    person = world.people.ensure_person(phone, "Vecina Prueba", doc)
    await world.lots.upsert_membership("VC3", "B14", person.id, MembershipRole.HOUSEHOLD)
    return person


async def _activate(client, phone, doc):
    return await client.post(f"{API}/auth/activate-pin", json={"username": phone, "document_id": doc})


async def test_member_without_dni_on_file_cannot_activate(client, world, pins):
    await _member(world, "955000111", None)
    r = await _activate(client, "955000111", "12345678")
    assert r.status_code == 409 and "DNI registrado" in r.json()["detail"]
    assert pins == {}


async def test_daily_pin_limit_per_person(client, world, pins):
    await _member(world, "955000222", "12345678")
    codes = [(await _activate(client, "955000222", "12345678")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


async def test_wrong_dni_three_times_blocks_ip(client, world, pins):
    await _member(world, "955000333", "12345678")
    codes = [(await _activate(client, "955000333", "87654321")).status_code for _ in range(3)]
    assert codes == [400, 400, 403]
    assert (await _activate(client, "955000333", "12345678")).status_code == 403
    assert pins == {}


async def test_invalid_document_format_and_ce_accepted(client, world, pins):
    await _member(world, "955000444", "CE00123456")
    assert (await _activate(client, "955000444", "12-34")).status_code == 422
    assert (await _activate(client, "955000444", "ce 00123456")).status_code == 200


async def test_anchor_must_provide_household_dni_and_admin_can_fix_it(client, h, world, pins):
    no_doc = await client.post(f"{API}/lots/B14/members", headers=h.anchor_b14, json={"phone": "955000555", "full_name": "Sin Dni"})
    assert no_doc.status_code == 422
    ok = await client.post(
        f"{API}/lots/B14/members", headers=h.anchor_b14,
        json={"phone": "955000555", "full_name": "Con Dni", "document_id": "4011-1222"},
    )
    assert ok.status_code == 201 and ok.json()["document_id"] == "******22"

    await client.post(f"{API}/lots/B14/members", headers=h.admin, json={"phone": "955000666", "full_name": "Pendiente"})
    assert (await _activate(client, "955000666", "40999888")).status_code == 409
    assert (await client.patch(f"{API}/lots/B14/members/955000666", headers=h.anchor_b14, json={"document_id": "40999888"})).status_code == 403
    fixed = await client.patch(f"{API}/lots/B14/members/955000666", headers=h.admin, json={"document_id": "40999888"})
    assert fixed.status_code == 200 and fixed.json()["document_id"] == "******88"
    assert (await _activate(client, "955000666", "40999888")).status_code == 200


async def test_admin_dni_change_resets_access_and_closes_sessions(client, h, world, pins):
    await _member(world, "955000888", "40100100")
    for _ in range(3):  # agota su límite diario
        assert (await _activate(client, "955000888", "40100100")).status_code == 200
    login = await client.post(f"{API}/auth/login", json={"username": "955000888", "password": pins["51955000888"]})
    assert login.status_code == 200
    old_token = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get(f"{API}/lots/my-memberships", headers=old_token)).status_code == 200

    members = await client.get(f"{API}/lots/B14/members", headers=h.admin)
    assert "40100100" not in members.text

    changed = await client.patch(f"{API}/lots/B14/members/955000888", headers=h.admin, json={"document_id": "40200200"})
    assert changed.status_code == 200 and changed.json()["document_id"] == "******00"

    assert (await client.get(f"{API}/lots/my-memberships", headers=old_token)).status_code == 401
    old_pin = pins["51955000888"]
    assert (await client.post(f"{API}/auth/login", json={"username": "955000888", "password": old_pin})).status_code == 401
    assert (await client.post(f"{API}/auth/lookup", json={"username": "955000888"})).json()["status"] == "NEEDS_DOCUMENT"
    assert (await _activate(client, "955000888", "40100100")).status_code == 400
    assert (await _activate(client, "955000888", "40200200")).status_code == 200
    relogin = await client.post(f"{API}/auth/login", json={"username": "955000888", "password": pins["51955000888"]})
    assert relogin.status_code == 200


async def test_filling_empty_dni_keeps_access_but_replacing_resets(client, h, world, pins):
    person = world.people.ensure_person("955000777", "Ya Activo", None, pin="55556666")
    await world.lots.upsert_membership("VC3", "B14", person.id, MembershipRole.HOUSEHOLD)
    login = await client.post(f"{API}/auth/login", json={"username": "955000777", "password": "55556666"})
    token = {"Authorization": f"Bearer {login.json()['access_token']}"}

    filled = await client.patch(f"{API}/lots/B14/members/955000777", headers=h.admin, json={"document_id": "40700700"})
    assert filled.status_code == 200 and filled.json()["document_id"] == "******00"
    # Completar un DNI vacío no cierra la sesión ni invalida el PIN
    assert (await client.get(f"{API}/lots/my-memberships", headers=token)).status_code == 200
    assert (await client.post(f"{API}/auth/login", json={"username": "955000777", "password": "55556666"})).status_code == 200

    # Guardar el mismo DNI tampoco resetea
    await client.patch(f"{API}/lots/B14/members/955000777", headers=h.admin, json={"document_id": "40700700"})
    assert (await client.post(f"{API}/auth/login", json={"username": "955000777", "password": "55556666"})).status_code == 200

    # Reemplazar un DNI existente sí restablece el acceso
    await client.patch(f"{API}/lots/B14/members/955000777", headers=h.admin, json={"document_id": "40800800"})
    assert (await client.post(f"{API}/auth/login", json={"username": "955000777", "password": "55556666"})).status_code == 401


async def test_anchor_changes_household_phone_and_access_resets(client, h, world, pins):
    person = world.people.ensure_person("955000900", "Angel Plasencia", "71234567", pin="77778888")
    await world.lots.upsert_membership("VC3", "B14", person.id, MembershipRole.HOUSEHOLD)
    login = await client.post(f"{API}/auth/login", json={"username": "955000900", "password": "77778888"})
    old_token = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Celular ocupado por otra persona -> 409
    taken = await client.patch(f"{API}/lots/B14/members/955000900", headers=h.anchor_b14, json={"phone": "934253226"})
    assert taken.status_code == 409

    changed = await client.patch(f"{API}/lots/B14/members/955000900", headers=h.anchor_b14, json={"phone": "955 000 901"})
    assert changed.status_code == 200 and changed.json()["phone"] == "955000901"
    assert changed.json()["full_name"] == "Angel Plasencia"  # misma persona, mismo DNI

    # Sesión vieja cerrada; el número viejo ya no existe; el nuevo activa con su DNI
    assert (await client.get(f"{API}/lots/my-memberships", headers=old_token)).status_code == 401
    assert (await client.post(f"{API}/auth/lookup", json={"username": "955000900"})).json()["status"] == "UNREGISTERED_NO_ACCOUNT"
    assert (await client.post(f"{API}/auth/lookup", json={"username": "955000901"})).json()["status"] == "NEEDS_DOCUMENT"
    act = await client.post(f"{API}/auth/activate-pin", json={"username": "955000901", "document_id": "71234567"})
    assert act.status_code == 200 and "51955000901" in pins


async def test_household_phone_change_permissions(client, h, world):
    hh = world.people.ensure_person("955000910", "Familiar", "40910910")
    await world.lots.upsert_membership("VC3", "B14", hh.id, MembershipRole.HOUSEHOLD)
    hh_headers = world.headers("955000910", __import__("app.models.user", fromlist=["UserRole"]).UserRole.RESIDENT, lot="B14", mem=MembershipRole.HOUSEHOLD)
    # Un HOUSEHOLD no puede editar a nadie
    assert (await client.patch(f"{API}/lots/B14/members/955000910", headers=hh_headers, json={"phone": "955000911"})).status_code == 403
    # El ANCHOR no puede cambiar el celular del titular ni el DNI de nadie
    assert (await client.patch(f"{API}/lots/B14/members/916220518", headers=h.anchor_b14, json={"phone": "955000912"})).status_code == 403
    assert (await client.patch(f"{API}/lots/B14/members/955000910", headers=h.anchor_b14, json={"document_id": "40000000"})).status_code == 403
    # El Admin sí puede cambiar el celular de cualquiera
    ok = await client.patch(f"{API}/lots/B14/members/955000910", headers=h.admin, json={"phone": "955000913"})
    assert ok.status_code == 200 and ok.json()["phone"] == "955000913"
