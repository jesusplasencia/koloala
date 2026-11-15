"""Pases grupales sin cuenta: el QR es la única puerta de entrada."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.user import MembershipRole, UserRole
from app.services.visit_service import VisitService
from test.conftest import GUARD

API = "/api/v1"


async def _seed_catalog(client: AsyncClient, h) -> None:
    r = await client.post(
        f"{API}/admin/catalog/bulk",
        headers=h.admin,
        json={
            "items": [
                {"lot_id": "A01", "display_name": "A01 - Panadería"},
                {"lot_id": "A02", "display_name": "A02 - Botica"},
                {"lot_id": "CF1", "display_name": "Canchita de Fútbol 1", "lot_type": "COMMON_AREA"},
                {"lot_id": "A03", "display_name": "A03 - Oculto", "is_public": False},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["created_count"] == 4


async def test_catalog_bulk_is_idempotent_and_public_showcase(client: AsyncClient, h, world):
    await _seed_catalog(client, h)
    again = await client.post(
        f"{API}/admin/catalog/bulk",
        headers=h.admin,
        json={"items": [{"lot_id": "A01", "display_name": "A01 - Panadería San José"}]},
    )
    assert again.json()["created_count"] == 0 and again.json()["updated_count"] == 1

    r = await client.get(f"{API}/public/villa")
    assert r.status_code == 200
    names = [i["display_name"] for i in r.json()["catalog"]]
    assert names == ["Canchita de Fútbol 1", "A01 - Panadería San José", "A02 - Botica"]
    body = r.text
    assert "916220518" not in body and "Jesus" not in body  # sin datos de residentes

    # No se puede convertir una casa en catálogo ni subir un LOT por aquí
    bad = await client.post(
        f"{API}/admin/catalog/bulk", headers=h.admin,
        json={"items": [{"lot_id": "B14", "display_name": "XX"}]},
    )
    assert bad.status_code == 409
    guard_try = await client.post(
        f"{API}/admin/catalog/bulk", headers=h.guard,
        json={"items": [{"lot_id": "A09", "display_name": "XX"}]},
    )
    assert guard_try.status_code == 403


async def test_family_invite_without_phones_and_92_year_old_vouched(client: AsyncClient, h, world):
    r = await client.post(
        f"{API}/visits/invite",
        headers=h.anchor_b14,
        json={
            "party_size_expected": 4,
            "note": "Familia de Canadá",
            "attendees": [
                {"first_names": "maría elena", "last_names": "plasencia", "doc_number": "40123456"},
                {"first_names": "José", "last_names": "Plasencia"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    invite = r.json()
    token, code = invite["token"], invite["short_code"]
    assert invite["destination_label"] == "Lote B14"
    assert invite["share_path"] == f"/v/{token}"

    # Quien recibe el enlace completa al resto (pasaporte canadiense) sin crear cuenta
    add = await client.post(
        f"{API}/public/visits/{token}/attendees",
        json={"attendees": [{"first_names": "Emily", "last_names": "Smith",
                             "doc_type": "PASAPORTE", "doc_number": "AB123456"}]},
    )
    assert add.status_code == 200
    view = add.json()
    assert view["attendee_names"] == ["MARÍA ELENA PLASENCIA", "JOSÉ PLASENCIA", "EMILY SMITH"]
    assert "40123456" not in add.text and "916220518" not in add.text

    # Garita escanea por código digitado
    look = await client.get(f"{API}/guard/visits/{code}", headers=h.guard)
    assert look.status_code == 200 and look.json()["authorized"] is True
    assert look.json()["host_name"] == "Jesus Plasencia"

    # Check-in: el abuelo de 92 años (José) sin DNI avalado por su hija (#1); llega un nieto no listado
    ci = await client.post(
        f"{API}/guard/visits/{token}/check-in",
        headers=h.guard,
        json={
            "attendees": [
                {"attendee_no": 1},
                {"attendee_no": 2, "vouched_by_no": 1},
                {"attendee_no": 3},
                {"person": {"first_names": "Lucas", "last_names": "Smith"}},
            ]
        },
    )
    assert ci.status_code == 200, ci.text
    body = ci.json()
    by_no = {a["attendee_no"]: a for a in body["attendees"]}
    assert by_no[1]["verification"] == "DOC_SHOWN" and by_no[1]["doc_display"] == "40123456"
    assert by_no[2]["verification"] == "VOUCHED" and by_no[2]["vouched_by_no"] == 1
    assert by_no[2]["doc_type"] == "NINGUNO"
    assert by_no[3]["verification"] == "DOC_SHOWN"
    assert by_no[4]["verification"] == "DECLARED"
    assert body["status"] == "USED" and body["checked_in_by_guard"] == GUARD

    # Un solo uso
    again = await client.post(
        f"{API}/guard/visits/{token}/check-in", headers=h.guard, json={"attendees": [{"attendee_no": 1}]}
    )
    assert again.status_code == 409

    log = await client.get(f"{API}/guard/visits/daily-log", headers=h.guard)
    assert log.json()["total_people_in"] == 4


async def test_invite_rules(client: AsyncClient, h, world):
    moroso = await client.post(f"{API}/visits/invite", headers=h.moroso, json={})
    assert moroso.status_code == 403
    stranger = await client.post(f"{API}/visits/invite", headers=world.headers("955555555", UserRole.RESIDENT, lot="B14", mem=MembershipRole.HOUSEHOLD), json={"lot_id": "B14"})
    assert stranger.status_code == 403
    guard = await client.post(f"{API}/visits/invite", headers=h.guard, json={"lot_id": "B14"})
    assert guard.status_code == 403


async def test_voucher_must_be_another_attendee_entering(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.anchor_b14,
                          json={"attendees": [{"first_names": "Abuelo"}]})
    token = r.json()["token"]
    bad = await client.post(
        f"{API}/guard/visits/{token}/check-in", headers=h.guard,
        json={"attendees": [{"attendee_no": 1, "vouched_by_no": 1}]},
    )
    assert bad.status_code == 422
    # El pase no se quemó: se puede corregir y entrar como DECLARED
    ok = await client.post(
        f"{API}/guard/visits/{token}/check-in", headers=h.guard,
        json={"attendees": [{"attendee_no": 1}]},
    )
    assert ok.status_code == 200 and ok.json()["attendees"][0]["verification"] == "DECLARED"


async def test_visitors_cannot_self_issue_qr(client: AsyncClient, h, world):
    await _seed_catalog(client, h)
    r = await client.post(
        f"{API}/public/visits",
        json={"destination_lot_id": "A01", "attendees": [{"first_names": "X"}]},
    )
    assert r.status_code in (404, 405)


async def test_guard_desk_ocr_dni_to_bakery(client: AsyncClient, h, world):
    """Visitante sin invitación: el vigilante lee el reverso del DNI (OCR) y lo registra a un servicio."""
    await _seed_catalog(client, h)
    r = await client.post(
        f"{API}/guard/visits/desk", headers=h.guard,
        json={"destination_lot_id": "a1", "vehicle_plate": "b7j-412", "attendees": [
            {"person": {"first_names": "Jesus Manuel", "last_names": "Plasencia",
                        "doc_number": "60998877", "doc_source": "OCR"}},
            {"person": {"first_names": "Rosa", "last_names": "Quispe"}}]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["origin"] == "GUARD_DESK" and body["status"] == "USED"
    assert body["destination_label"] == "A01 - Panadería"
    assert body["vehicle_plate"] == "B7J412"
    first, second = body["attendees"]
    assert first["full_name"] == "JESUS MANUEL PLASENCIA"
    assert first["doc_source"] == "OCR" and first["verification"] == "DOC_SHOWN"
    assert second["doc_source"] is None and second["verification"] == "DECLARED"

    manual = await client.post(
        f"{API}/guard/visits/desk", headers=h.guard,
        json={"destination_lot_id": "CF1", "attendees": [
            {"person": {"first_names": "Ana", "doc_number": "12345678"}}]},
    )
    assert manual.json()["attendees"][0]["doc_source"] == "MANUAL"


async def test_guard_desk_only_catalog_services(client: AsyncClient, h, world):
    await _seed_catalog(client, h)
    entry = {"attendees": [{"person": {"first_names": "X"}}]}
    house = await client.post(f"{API}/guard/visits/desk", headers=h.guard, json={"destination_lot_id": "B14", **entry})
    assert house.status_code == 422 and "invitación" in house.json()["detail"]
    hidden = await client.post(f"{API}/guard/visits/desk", headers=h.guard, json={"destination_lot_id": "A03", **entry})
    assert hidden.status_code == 422
    missing = await client.post(f"{API}/guard/visits/desk", headers=h.guard, json={"destination_lot_id": "ZZ9", **entry})
    assert missing.status_code == 404
    bad_dni = await client.post(
        f"{API}/guard/visits/desk", headers=h.guard,
        json={"destination_lot_id": "A01", "attendees": [{"person": {"first_names": "X", "doc_number": "123"}}]},
    )
    assert bad_dni.status_code == 422


async def test_villa_isolation(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={})
    token = r.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://vc2.lasvillas.club") as vc2:
        look = await vc2.get(f"{API}/guard/visits/{token}", headers=h.guard_vc2)
        assert look.status_code == 404
        pub = await vc2.get(f"{API}/public/visits/{token}")
        assert pub.status_code == 404


async def test_access_search_audit_and_90_day_shadow(client: AsyncClient, h, world):
    await _seed_catalog(client, h)
    r = await client.post(
        f"{API}/guard/visits/desk", headers=h.guard,
        json={"destination_lot_id": "A01", "vehicle_plate": "XYZ-999", "attendees": [
            {"person": {"first_names": "Delivery", "last_names": "Sospechoso", "doc_number": "77665544"}}]},
    )
    assert r.status_code == 201

    no_filter = await client.get(f"{API}/admin/access-search", headers=h.admin)
    assert no_filter.status_code == 422
    guard_try = await client.get(f"{API}/admin/access-search?plate=XYZ999", headers=h.guard)
    assert guard_try.status_code == 403

    by_plate = await client.get(f"{API}/admin/access-search?plate=xyz 999", headers=h.admin)
    assert by_plate.json()["result_count"] == 1
    assert by_plate.json()["items"][0]["attendees"][0]["doc_display"] == "77665544"
    assert len(world.visits.data_access_log) == 1
    assert world.visits.data_access_log[0]["filters"]["plate"] is True
    assert "XYZ" not in str(world.visits.data_access_log[0])  # el log no guarda el valor buscado

    # 91 días después: el DNI y la placa pasan a shadow, pero el HMAC sigue permitiendo buscar
    service = VisitService(world.visits)
    shadowed = await service.shadow_old_pii(now=datetime.now(UTC) + timedelta(days=91))
    assert shadowed == 1
    by_dni = await client.get(f"{API}/admin/access-search?dni=77665544", headers=h.admin)
    item = by_dni.json()["items"][0]
    assert item["attendees"][0]["doc_display"] == "******44"
    assert item["vehicle_plate"] is None
    assert "77665544" not in by_dni.text
    by_plate_after = await client.get(f"{API}/admin/access-search?plate=XYZ999", headers=h.admin)
    assert by_plate_after.json()["result_count"] == 1
    assert await service.shadow_old_pii(now=datetime.now(UTC) + timedelta(days=91)) == 0


async def test_taxi_and_delivery_require_plate_without_burning_pass(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={"kind": "TAXI"})
    assert r.status_code == 201 and r.json()["kind"] == "TAXI"
    minutes = (datetime.fromisoformat(r.json()["valid_until"]) - datetime.fromisoformat(r.json()["valid_from"]))
    assert minutes == timedelta(minutes=30)
    token = r.json()["token"]
    entry = {"attendees": [{"person": {"first_names": "Chofer"}}]}
    no_plate = await client.post(f"{API}/guard/visits/{token}/check-in", headers=h.guard, json=entry)
    assert no_plate.status_code == 409 and "PLACA" in no_plate.json()["detail"]
    ok = await client.post(f"{API}/guard/visits/{token}/check-in", headers=h.guard,
                           json={**entry, "vehicle_plate": "B7J-412"})
    assert ok.status_code == 200 and ok.json()["vehicle_plate"] == "B7J412"

    d = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={"kind": "DELIVERY"})
    span = datetime.fromisoformat(d.json()["valid_until"]) - datetime.fromisoformat(d.json()["valid_from"])
    assert span == timedelta(minutes=45)


async def test_household_creates_guest_taxi_delivery(client: AsyncClient, h, world):
    person = world.people.ensure_person("980180219", "Angel")
    await world.lots.upsert_membership("VC3", "B14", person.id, MembershipRole.HOUSEHOLD)
    angel = world.headers("980180219", UserRole.RESIDENT, lot="B14", mem=MembershipRole.HOUSEHOLD)
    for kind in ("GUEST", "TAXI", "DELIVERY"):
        r = await client.post(f"{API}/visits/invite", headers=angel, json={"kind": kind})
        assert r.status_code == 201, (kind, r.text)


async def test_expired_pass_is_denied_and_marked(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={"kind": "TAXI"})
    token = r.json()["token"]
    stored = world.visits._visits[("VC3", token)]
    stored.valid_until = datetime.now(UTC) - timedelta(minutes=1)
    ci = await client.post(f"{API}/guard/visits/{token}/check-in", headers=h.guard,
                           json={"attendees": [{"person": {"first_names": "X"}}], "vehicle_plate": "AAA111"})
    assert ci.status_code == 409 and "venció" in ci.json()["detail"]
    assert world.visits._visits[("VC3", token)].status == "EXPIRED"


async def test_denied_if_lot_becomes_delinquent_before_arrival(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.anchor_b14, json={})
    token = r.json()["token"]
    lot = world.lots._lots[("VC3", "B14")]
    world.lots._lots[("VC3", "B14")] = lot.model_copy(update={"is_maintenance_up_to_date": False})
    ci = await client.post(f"{API}/guard/visits/{token}/check-in", headers=h.guard,
                           json={"attendees": [{"person": {"first_names": "X"}}]})
    assert ci.status_code == 409 and "mantenimiento" in ci.json()["detail"]
    look = await client.get(f"{API}/guard/visits/{token}", headers=h.guard)
    assert look.json()["status"] == "ACTIVE"  # no se quemó
