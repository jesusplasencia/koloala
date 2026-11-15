"""Carnet QR del residente, ingresos repetidos, DOC_DIGITAL, tope de HOUSEHOLD y bitácora enmascarada."""

import time
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.user import MembershipRole, UserRole
from app.services.entry_service import build_carnet_payload
from test.conftest import GUARD, JESUS

API = "/api/v1"


async def _add_membership(world, lot, phone, name, role):
    person = world.people.ensure_person(phone, name)
    return await world.lots.upsert_membership("VC3", lot, person.id, role)


async def test_admin_owner_enters_with_rotating_carnet_many_times(client: AsyncClient, h, world):
    # Jesús también es HOUSEHOLD del F08 (lote con deuda): un solo carnet, ve ambos lotes
    await _add_membership(world, "F08", "916220518", "Jesus Plasencia", MembershipRole.HOUSEHOLD)
    carnet = await client.get(f"{API}/me/carnet", headers=h.jesus)
    assert carnet.status_code == 200, carnet.text
    c = carnet.json()
    assert c["window_seconds"] == 30 and 0 < c["expires_in"] <= 30
    assert {(lot["lot_id"], lot["membership_role"]) for lot in c["lots"]} == {
        ("B14", "ANCHOR"), ("F08", "HOUSEHOLD")}

    for _ in range(3):  # el carnet no se gasta
        r = await client.post(f"{API}/guard/resident-entry", headers=h.guard,
                              json={"qr_payload": c["payload"], "vehicle_plate": "abc-123"})
        assert r.status_code == 201, r.text
    body = r.json()
    assert body["person_name"] == "Jesus Plasencia" and body["method"] == "QR"
    assert body["message"] == "RESIDENTE: Jesus Plasencia - B14, F08"
    assert body["warnings"] == ["Lote F08 con deuda de mantenimiento"]  # advierte, no bloquea
    assert "916220518" not in r.text  # el guardia no ve el celular

    mine = await client.get(f"{API}/me/entries", headers=h.jesus)
    assert len(mine.json()["items"]) == 3
    log = await client.get(f"{API}/guard/visits/daily-log", headers=h.guard)
    assert log.json()["resident_entries"] == 3
    search = await client.get(f"{API}/admin/access-search?plate=ABC123", headers=h.admin)
    assert len(search.json()["resident_entries"]) == 3


async def test_expired_tampered_and_foreign_villa_carnet(client: AsyncClient, h, world):
    jesus_id = world.people.ensure_person(JESUS).id
    moroso_id = world.people.ensure_person("999111222").id
    old, _ = build_carnet_payload("VC3", jesus_id, now_s=time.time() - 3 * settings.resident_qr_window_seconds)
    r = await client.post(f"{API}/guard/resident-entry", headers=h.guard, json={"qr_payload": old})
    assert r.status_code == 409
    good, _ = build_carnet_payload("VC3", jesus_id)
    forged = good.replace(jesus_id, moroso_id)
    r = await client.post(f"{API}/guard/resident-entry", headers=h.guard, json={"qr_payload": forged})
    assert r.status_code == 409
    vc2, _ = build_carnet_payload("VC2", world.people.ensure_person("988777666").id)
    r = await client.post(f"{API}/guard/resident-entry", headers=h.guard, json={"qr_payload": vc2})
    assert r.status_code == 409
    resident_try = await client.post(f"{API}/guard/resident-entry", headers=h.jesus, json={"qr_payload": good})
    assert resident_try.status_code == 201  # SUPER_ADMIN también puede operar garita


async def test_no_membership_no_carnet_and_deactivated_loses_access(client: AsyncClient, h, world):
    assert (await client.get(f"{API}/me/carnet", headers=world.headers("955555555", UserRole.RESIDENT))).status_code == 403
    await _add_membership(world, "B14", "955555555", "Angel", MembershipRole.HOUSEHOLD)
    payload = (await client.get(f"{API}/me/carnet", headers=world.headers("955555555", UserRole.RESIDENT))).json()["payload"]
    await world.lots.deactivate_membership("VC3", "B14", world.people.ensure_person("955555555").id)
    r = await client.post(f"{API}/guard/resident-entry", headers=h.guard, json={"qr_payload": payload})
    assert r.status_code == 403


async def test_manual_entry_without_phone(client: AsyncClient, h, world):
    ok = await client.post(f"{API}/guard/resident-entry", headers=h.guard,
                           json={"phone": "916 220 518", "lot_id": "b14"})
    assert ok.status_code == 201 and ok.json()["method"] == "MANUAL"
    wrong = await client.post(f"{API}/guard/resident-entry", headers=h.guard,
                              json={"phone": "916220518", "lot_id": "F08"})
    assert wrong.status_code == 404


async def test_household_cap_per_lot(client: AsyncClient, monkeypatch, h, world):
    monkeypatch.setattr(settings, "max_household_per_lot", 2)
    owner = h.anchor_b14
    codes = []
    for i in range(3):
        r = await client.post(f"{API}/lots/B14/members", headers=owner,
                              json={"phone": f"98000000{i}", "full_name": f"Familiar {i}", "document_id": f"7000000{i}"})
        codes.append(r.status_code)
    assert codes[:2] == [codes[0], codes[0]] and codes[0] in (200, 201)
    assert codes[2] == 409
    # El Admin sí puede superar el tope
    r = await client.post(f"{API}/lots/B14/members", headers=h.admin,
                          json={"phone": "980000009", "full_name": "Extra"})
    assert r.status_code in (200, 201)


async def test_doc_digital_and_masked_daily_log(client: AsyncClient, h, world):
    r = await client.post(f"{API}/visits/invite", headers=h.jesus,
                          json={"attendees": [{"first_names": "Ana", "doc_number": "45678912"}]})
    token = r.json()["token"]
    ci = await client.post(f"{API}/guard/visits/{token}/check-in", headers=h.guard,
                           json={"attendees": [{"attendee_no": 1, "verification": "DOC_DIGITAL"}]})
    assert ci.status_code == 200 and ci.json()["attendees"][0]["verification"] == "DOC_DIGITAL"

    masked = await client.get(f"{API}/guard/visits/daily-log", headers=h.guard)
    assert masked.json()["items"][0]["attendees"][0]["doc_display"] == "******12"
    assert "45678912" not in masked.text and world.visits.data_access_log == []

    revealed = await client.get(f"{API}/guard/visits/daily-log?reveal=true", headers=h.guard)
    assert revealed.json()["items"][0]["attendees"][0]["doc_display"] == "45678912"
    assert world.visits.data_access_log[-1]["action"] == "DAILY_LOG_REVEAL"
    assert world.visits.data_access_log[-1]["actor"] == GUARD
