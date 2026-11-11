from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.auth import get_lot_repo, get_user_repo
from app.api.v1.lots import get_lots_service
from app.api.v1.passes import get_access_service
from app.core.security import create_access_token
from app.main import app
from app.models.access import PassStatus, PassType
from app.models.user import AttackType, UserRole
from app.repositories.access_repo import InMemoryAccessRepository
from app.repositories.lots_repo import InMemoryLotRepository
from app.repositories.users_repo import InMemoryUserRepository
from app.services.access_service import AccessService
from app.services.lots_service import LotsService


@pytest.fixture
def user_repo() -> InMemoryUserRepository:
    """Crea un repositorio de usuarios limpio (VC3 y VC2) por cada test."""
    return InMemoryUserRepository()


@pytest.fixture
def lot_repo() -> InMemoryLotRepository:
    """Crea un repositorio de lotes limpio con llaves compuestas (villa_id, lot_id)."""
    return InMemoryLotRepository()


@pytest.fixture
def repo(lot_repo: InMemoryLotRepository) -> InMemoryAccessRepository:
    """Crea un repositorio de accesos conectado al mismo padrón de lotes y membresías 3NF (lot_repo)."""
    return InMemoryAccessRepository(
        lots_store=lot_repo.lots_store,
        memberships_store=lot_repo.memberships_store,
    )


@pytest.fixture
async def client(
    repo: InMemoryAccessRepository,
    lot_repo: InMemoryLotRepository,
    user_repo: InMemoryUserRepository,
):
    """Inyecta los 3 repositorios limpios en FastAPI durante la ejecución de cada test."""
    app.dependency_overrides[get_access_service] = lambda: AccessService(repo=repo)
    app.dependency_overrides[get_lots_service] = lambda: LotsService(
        repo=lot_repo, user_repo=user_repo
    )
    app.dependency_overrides[get_user_repo] = lambda: user_repo
    app.dependency_overrides[get_lot_repo] = lambda: lot_repo
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://vc3.lasvillas.club"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def b14_headers() -> dict[str, str]:
    token = create_access_token(
        username="B14", role=UserRole.RESIDENT, villa_id="VC3", lot_id="B14"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def f08_headers() -> dict[str, str]:
    token = create_access_token(
        username="F08", role=UserRole.RESIDENT, villa_id="VC3", lot_id="F08"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def guard_headers() -> dict[str, str]:
    token = create_access_token(
        username="GUARDIA1", role=UserRole.GUARD, villa_id="VC3", lot_id=None
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    token = create_access_token(
        username="ADMIN", role=UserRole.ADMIN, villa_id="VC3", lot_id=None
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. HAPPY PATH: Login VC3+B14 -> Crea pase -> Vigilante verifica con DNI + Placa
# ---------------------------------------------------------------------------
async def test_create_and_verify_pass_happy_path(
    client: AsyncClient,
    b14_headers: dict[str, str],
    guard_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": "DELIVERY"},
    )
    assert create_resp.status_code == 201
    pass_data = create_resp.json()
    assert len(pass_data["code"]) == 6
    assert pass_data["villa_id"] == "VC3"
    assert pass_data["lot_id"] == "B14"
    assert pass_data["status"] == PassStatus.ACTIVE

    verify_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={
            "code": pass_data["code"],
            "visitor_dni": "45891230",
            "vehicle_plate": "b7j-412",
        },
    )
    assert verify_resp.status_code == 200
    verification = verify_resp.json()
    assert verification["authorized"] is True
    assert verification["villa_id"] == "VC3"
    assert verification["lot_id"] == "B14"
    assert verification["owner_name"] == "Jesus Plasencia"
    assert verification["checked_in_dni"] == "45891230"
    assert verification["checked_in_plate"] == "B7J-412"


# ---------------------------------------------------------------------------
# 2. REGLA DE GARITA: Taxi/Delivery sin placa es retenido SIN quemar el código
# ---------------------------------------------------------------------------
async def test_taxi_requires_plate_without_burning_pass(
    client: AsyncClient,
    b14_headers: dict[str, str],
    guard_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": "TAXI", "visitor_info": "Uber"},
    )
    code = create_resp.json()["code"]

    missing_plate_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "41223344"},
    )
    assert missing_plate_resp.json()["authorized"] is False
    assert "FALTA PLACA" in missing_plate_resp.json()["message"]

    ok_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "41223344", "vehicle_plate": "ABC-123"},
    )
    assert ok_resp.json()["authorized"] is True


# ---------------------------------------------------------------------------
# 3. REGLA ANTI-REUSO (Single-Use Pass): Un código quemado no puede reusarse
# ---------------------------------------------------------------------------
async def test_cannot_reuse_already_used_pass(
    client: AsyncClient,
    b14_headers: dict[str, str],
    guard_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": "GUEST", "visitor_info": "Visita peatonal"},
    )
    code = create_resp.json()["code"]

    first_verify = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "70112233"},
    )
    assert first_verify.json()["authorized"] is True

    second_verify = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "70112233"},
    )
    assert second_verify.status_code == 200
    assert second_verify.json()["authorized"] is False
    assert "ya fue utilizado" in second_verify.json()["message"]


# ---------------------------------------------------------------------------
# 4. REGLA DE MOROSIDAD (403 Forbidden): Vecino VC3+F08 con deuda no genera pases
# ---------------------------------------------------------------------------
async def test_delinquent_lot_cannot_create_pass(
    client: AsyncClient,
    f08_headers: dict[str, str],
) -> None:
    resp = await client.post(
        "/api/v1/passes",
        headers=f08_headers,
        json={"pass_type": "DELIVERY", "visitor_info": "PedidosYa"},
    )
    assert resp.status_code == 403
    assert "deuda de mantenimiento" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. REGLA DE EXPIRACIÓN (TTL Vencido): Taxi llega tarde a la garita
# ---------------------------------------------------------------------------
async def test_expired_pass_is_denied_at_gatehouse(
    client: AsyncClient,
    repo: InMemoryAccessRepository,
    b14_headers: dict[str, str],
    guard_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": PassType.TAXI},
    )
    code = create_resp.json()["code"]

    stored_pass = await repo.get_pass_by_code("VC3", code)
    assert stored_pass is not None
    stored_pass.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await repo.save_pass(stored_pass)

    verify_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "45891230", "vehicle_plate": "XYZ-999"},
    )
    assert verify_resp.json()["authorized"] is False
    assert "expirado" in verify_resp.json()["message"]

    updated_pass = await repo.get_pass_by_code("VC3", code)
    assert updated_pass is not None
    assert updated_pass.status == PassStatus.EXPIRED


# ---------------------------------------------------------------------------
# 6. REGLA DE REVOCACIÓN EN GARITA (Vía Admin API): Vecino cae en mora
# ---------------------------------------------------------------------------
async def test_pass_denied_if_admin_marks_lot_delinquent_before_arrival(
    client: AsyncClient,
    b14_headers: dict[str, str],
    guard_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": PassType.GUEST, "visitor_info": "Invitado: Carlos"},
    )
    code = create_resp.json()["code"]

    patch_resp = await client.patch(
        "/api/v1/lots/B14/maintenance",
        headers=admin_headers,
        json={"is_maintenance_up_to_date": False},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_maintenance_up_to_date"] is False

    verify_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={"code": code, "visitor_dni": "45891230"},
    )
    assert verify_resp.json()["authorized"] is False
    assert "habilitación de mantenimiento" in verify_resp.json()["message"]


# ---------------------------------------------------------------------------
# 7. ADMIN BULK SYNC & RESET: Clave de 8 dígitos, Link WhatsApp y Reset Admin
# ---------------------------------------------------------------------------
async def test_admin_bulk_upsert_generates_credentials_and_allows_login(
    client: AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    bulk_resp = await client.put(
        "/api/v1/lots/bulk",
        headers=admin_headers,
        json={
            "lots": [
                {
                    "lot_id": "F08",
                    "owner_name": "Vecino Ya Pagó",
                    "owner_phone": "999111222",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "P20",
                    "owner_name": "Familia Torres",
                    "owner_phone": "987 654 321",
                    "owner_email": "torres.p20@gmail.com",
                    "is_maintenance_up_to_date": True,
                },
            ]
        },
    )
    assert bulk_resp.status_code == 200
    data = bulk_resp.json()
    assert data["villa_id"] == "VC3"
    assert data["processed_count"] == 2
    assert data["created_count"] == 1
    assert data["updated_count"] == 1
    assert len(data["generated_credentials"]) == 1

    p20_cred = data["generated_credentials"][0]
    assert p20_cred["villa_id"] == "VC3"
    assert p20_cred["lot_id"] == "P20"
    assert p20_cred["owner_email"] == "torres.p20@gmail.com"
    assert p20_cred["login_url"] == "https://vc3.lasvillas.club"
    assert len(p20_cred["initial_password"]) == 8
    assert p20_cred["initial_password"].isdigit()
    assert p20_cred["whatsapp_share_url"].startswith("https://wa.me/51987654321?text=")

    # El vecino inicia sesión con su contraseña numérica de 8 dígitos
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "P20", "password": p20_cred["initial_password"]},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert token_data["expires_in_days"] == 30
    assert token_data["villa_id"] == "VC3"
    assert token_data["lot_id"] == "P20"

    # Si luego el vecino olvida su clave, el Admin la resetea en 1 clic
    reset_resp = await client.post(
        "/api/v1/lots/P20/reset-password",
        headers=admin_headers,
    )
    assert reset_resp.status_code == 200
    reset_cred = reset_resp.json()
    assert len(reset_cred["initial_password"]) == 8
    assert reset_cred["initial_password"].isdigit()
    assert reset_cred["initial_password"] != p20_cred["initial_password"]

    # Y la nueva clave de 8 dígitos funciona de inmediato
    relogin_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "P20", "password": reset_cred["initial_password"]},
    )
    assert relogin_resp.status_code == 200


# ---------------------------------------------------------------------------
# 8. SEGURIDAD: Password Spraying bloquea la IP al 3er fallo y descubre al vecino
# ---------------------------------------------------------------------------
async def test_password_spraying_blocks_attacker_ip_and_identifies_suspect_lot(
    client: AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    attacker_ip = "190.236.99.88"
    attacker_headers = {"X-Forwarded-For": attacker_ip, "User-Agent": "Android-Chistoso"}

    legit_login = await client.post(
        "/api/v1/auth/login",
        headers=attacker_headers,
        json={"username": "F08", "password": "VC3-F08"},
    )
    assert legit_login.status_code == 200

    r1 = await client.post(
        "/api/v1/auth/login",
        headers=attacker_headers,
        json={"username": "B14", "password": "claveFalsa1"},
    )
    assert r1.status_code == 401

    r2 = await client.post(
        "/api/v1/auth/login",
        headers=attacker_headers,
        json={"username": "U28", "password": "claveFalsa1"},
    )
    assert r2.status_code == 401

    r3 = await client.post(
        "/api/v1/auth/login",
        headers=attacker_headers,
        json={"username": "P20", "password": "claveFalsa1"},
    )
    assert r3.status_code == 403
    assert "PASSWORD_SPRAYING" in r3.json()["detail"]

    b14_real = await client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "190.236.10.14"},
        json={"username": "B14", "password": "VC3-B14"},
    )
    assert b14_real.status_code == 200

    sec_resp = await client.get(
        "/api/v1/auth/security/blocked-ips", headers=admin_headers
    )
    assert sec_resp.status_code == 200
    reports = sec_resp.json()
    assert len(reports) == 1
    assert reports[0]["villa_id"] == "VC3"
    assert reports[0]["ip_address"] == attacker_ip
    assert reports[0]["attack_type"] == AttackType.PASSWORD_SPRAYING
    assert reports[0]["attempted_usernames"] == ["B14", "U28", "P20"]
    assert reports[0]["suspected_lots_from_previous_logins"] == ["F08"]

    # 5. El Administrador desbloquea la IP (vía UPDATE de estado, sin perder correlación de F08)
    unblock_resp = await client.delete(
        f"/api/v1/auth/security/blocked-ips/{attacker_ip}",
        headers=admin_headers,
    )
    assert unblock_resp.status_code == 200

    # La lista de IPs bloqueadas queda vacía y F08 puede volver a autenticarse desde su IP
    sec_after = await client.get(
        "/api/v1/auth/security/blocked-ips", headers=admin_headers
    )
    assert sec_after.json() == []

    login_after_unblock = await client.post(
        "/api/v1/auth/login",
        headers=attacker_headers,
        json={"username": "F08", "password": "VC3-F08"},
    )
    assert login_after_unblock.status_code == 200


# ---------------------------------------------------------------------------
# 9. MULTI-TENANT (VC3 vs VC2): Aislamiento total por llave compuesta (villa_id, lot_id)
# ---------------------------------------------------------------------------
async def test_multi_villa_isolation_between_vc3_and_vc2(
    client: AsyncClient,
    b14_headers: dict[str, str],
) -> None:
    # 1. En VC3, el lote B14 está al día y puede crear su pase
    vc3_pass = await client.post(
        "/api/v1/passes",
        headers=b14_headers,
        json={"pass_type": "DELIVERY"},
    )
    assert vc3_pass.status_code == 201
    code_vc3 = vc3_pass.json()["code"]

    # 2. En VC2 (vc2.lasvillas.club), el lote B14 es de otra familia y está en mora (False)
    vc2_login = await client.post(
        "/api/v1/auth/login",
        headers={"X-Villa-Id": "VC2"},
        json={"username": "B14", "password": "VC2-B14"},
    )
    assert vc2_login.status_code == 200
    vc2_b14_token = vc2_login.json()["access_token"]

    vc2_pass_attempt = await client.post(
        "/api/v1/passes",
        headers={
            "Authorization": f"Bearer {vc2_b14_token}",
            "X-Villa-Id": "VC2",
        },
        json={"pass_type": "DELIVERY"},
    )
    assert vc2_pass_attempt.status_code == 403

    # 3. Un token de VC3 no puede operar si entra por el subdominio/header de VC2
    cross_tenant_attempt = await client.post(
        "/api/v1/passes",
        headers={**b14_headers, "X-Villa-Id": "VC2"},
        json={"pass_type": "TAXI"},
    )
    assert cross_tenant_attempt.status_code == 403
    assert "pertenece a VC3" in cross_tenant_attempt.json()["detail"]

    # 4. El guardia de VC2 no puede verificar un código generado en VC3
    vc2_guard_token = create_access_token(
        username="GUARDIA1", role=UserRole.GUARD, villa_id="VC2", lot_id=None
    )
    vc2_verify = await client.post(
        "/api/v1/passes/verify",
        headers={
            "Authorization": f"Bearer {vc2_guard_token}",
            "X-Villa-Id": "VC2",
        },
        json={"code": code_vc3, "visitor_dni": "45891230", "vehicle_plate": "ABC-123"},
    )
    assert vc2_verify.json()["authorized"] is False
    assert "no existe en VC2" in vc2_verify.json()["message"]


# ---------------------------------------------------------------------------
# 10. PAGINACIÓN DE LOTES: page=0.., limit=50 por defecto y filtro opcional
# ---------------------------------------------------------------------------
async def test_lots_pagination_and_filtering(
    client: AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    # Semilla en VC3 tiene 2 lotes: B14 (True) y F08 (False). Agregamos 3 más -> Total 5
    await client.put(
        "/api/v1/lots/bulk",
        headers=admin_headers,
        json={
            "lots": [
                {"lot_id": "A01", "owner_name": "Fam A01", "is_maintenance_up_to_date": True},
                {"lot_id": "P20", "owner_name": "Fam P20", "is_maintenance_up_to_date": True},
                {"lot_id": "U28", "owner_name": "Fam U28", "is_maintenance_up_to_date": False},
            ]
        },
    )

    # 1. Por defecto (page=0, limit=50): devuelve los 5 ordenados alfabéticamente
    default_resp = await client.get("/api/v1/lots", headers=admin_headers)
    assert default_resp.status_code == 200
    body = default_resp.json()
    assert body["total"] == 5
    assert body["page"] == 0
    assert body["limit"] == 50
    assert body["total_pages"] == 1
    assert body["has_next"] is False
    assert [item["lot_id"] for item in body["items"]] == ["A01", "B14", "F08", "P20", "U28"]

    # 2. Paginando de 2 en 2: page=0 (A01, B14), page=1 (F08, P20), page=2 (U28)
    page0 = (await client.get("/api/v1/lots?page=0&limit=2", headers=admin_headers)).json()
    assert [x["lot_id"] for x in page0["items"]] == ["A01", "B14"]
    assert page0["total_pages"] == 3
    assert page0["has_next"] is True

    page2 = (await client.get("/api/v1/lots?page=2&limit=2", headers=admin_headers)).json()
    assert [x["lot_id"] for x in page2["items"]] == ["U28"]
    assert page2["has_next"] is False

    # 3. Filtrando solo morosos (is_maintenance_up_to_date=false) -> F08 y U28
    delinquents = (
        await client.get(
            "/api/v1/lots?is_maintenance_up_to_date=false", headers=admin_headers
        )
    ).json()
    assert delinquents["total"] == 2
    assert [x["lot_id"] for x in delinquents["items"]] == ["F08", "U28"]


# ---------------------------------------------------------------------------
# 11. AUDIT LOG ROTATION & FINOPS: Límite de 5 .gz en PC Local y Borrado Total tras S3
# ---------------------------------------------------------------------------
def test_audit_gz_rotation_cleanup_and_s3_deletion(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import audit
    from app.core.config import settings

    # Apuntamos audit_log_dir a una carpeta temporal limpia para el test
    monkeypatch.setattr(settings, "audit_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_local_gz_files", 5)
    monkeypatch.setattr(settings, "s3_audit_bucket", None)

    # 1. MODO PC LOCAL (Sin S3): Simulamos 8 cierres horarios/shutdowns seguidos
    for i in range(8):
        raw_file = tmp_path / "audit.jsonl"
        raw_file.write_text(f'{{"event": "TEST_{i}"}}\n', encoding="utf-8")
        dest_file = tmp_path / f"audit.jsonl.2026-11-05_{i:02d}"
        audit._hourly_s3_rotator(str(raw_file), str(dest_file))

    # Verificamos que el archivo .jsonl original se borró y solo quedan máximo 5 archivos .gz
    remaining_local_gz = sorted(tmp_path.glob("*.gz"))
    assert not (tmp_path / "audit.jsonl").exists()
    assert len(remaining_local_gz) == 5

    # 2. MODO CONTENEDOR AWS ECS (Con S3_AUDIT_BUCKET activo):
    # Simulamos que _upload_gz_to_s3 sube a S3 y borra cada archivo .gz local
    uploaded_files: list[str] = []

    def fake_s3_upload(gz_path) -> bool:
        uploaded_files.append(gz_path.name)
        gz_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(settings, "s3_audit_bucket", "lasvillas-audit-bucket")
    monkeypatch.setattr(audit, "_upload_gz_to_s3", fake_s3_upload)

    raw_file = tmp_path / "audit.jsonl"
    raw_file.write_text('{"event": "FINAL_SHUTDOWN"}\n', encoding="utf-8")
    dest_file = tmp_path / "audit.jsonl.2026-11-05_99"
    audit._hourly_s3_rotator(str(raw_file), str(dest_file))

    # Sube el nuevo .gz + los 5 que estaban pendientes y deja CERO archivos .gz en el contenedor
    assert len(uploaded_files) == 6
    assert list(tmp_path.glob("*.gz")) == []


# ---------------------------------------------------------------------------
# 12. LOGIN INTELIGENTE 2 PASOS: Lookup por lote -> Activación WhatsApp Just-In-Time -> Login
# ---------------------------------------------------------------------------
async def test_two_step_login_lookup_and_just_in_time_whatsapp_pin(
    client: AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import auth as auth_module

    captured_pins: dict[str, str] = {}

    async def fake_send_whatsapp_pin(
        *,
        phone: str,
        villa_id: str,
        lot_id: str,
        owner_name: str,
        pin_password: str,
    ) -> tuple[bool, str]:
        captured_pins[f"{villa_id}:{lot_id}:{phone}"] = pin_password
        return True, "evolution_api_mock"

    monkeypatch.setattr(
        auth_module, "send_whatsapp_pin_notification", fake_send_whatsapp_pin
    )

    # 1. El Admin carga el lote P21 con su celular en el padrón (aún nunca ha iniciado sesión)
    await client.put(
        "/api/v1/lots/bulk",
        headers=admin_headers,
        json={
            "lots": [
                {
                    "lot_id": "P21",
                    "owner_name": "Familia Vargas",
                    "owner_phone": "987654321",
                    "is_maintenance_up_to_date": True,
                }
            ]
        },
    )

    # 2. Paso 1 del vecino: escribe solo "P21" en /api/v1/auth/lookup
    lookup_resp = await client.post("/api/v1/auth/lookup", json={"username": "P21"})
    assert lookup_resp.status_code == 200
    lookup_data = lookup_resp.json()
    assert lookup_data["status"] == "NEEDS_PHONE_ACTIVATION"
    assert "masked_phone_hint" not in lookup_data  # no se filtran dígitos del teléfono
    assert lookup_data["owner_name"] == "Familia Vargas"

    # 3. Si alguien ingresa un celular incorrecto, falla con 401
    wrong_phone = await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "P21", "phone": "900000000"},
    )
    assert wrong_phone.status_code == 401

    # 4. El vecino confirma su celular real -> se genera el PIN Just-In-Time en RAM y sale por WhatsApp
    act_resp = await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "P21", "phone": "987 654 321"},
    )
    assert act_resp.status_code == 200
    sent_pin = captured_pins.get("VC3:P21:51987654321")
    assert sent_pin is not None
    assert len(sent_pin) == 8 and sent_pin.isdigit()

    # 5. El vecino ingresa con el PIN recibido en su WhatsApp
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "P21", "password": sent_pin},
    )
    assert login_resp.status_code == 200

    # 6. A partir de ahora (last_login_at != None), /lookup ya va directo a READY_FOR_PASSWORD
    second_lookup = await client.post("/api/v1/auth/lookup", json={"username": "P21"})
    assert second_lookup.json()["status"] == "READY_FOR_PASSWORD"


# ---------------------------------------------------------------------------
# 13. ADMINS OFICIALES TELEFÓNICOS: Super Admin (916220518) y Admin VC3 (934253226)
# ---------------------------------------------------------------------------
async def test_official_phone_admins_916220518_and_934253226(
    client: AsyncClient,
) -> None:
    # 1. Login del Super Admin de la App (Jesús Plasencia - 916220518)
    super_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "916220518", "password": "91622051"},
    )
    assert super_login.status_code == 200
    super_data = super_login.json()
    assert super_data["role"] == UserRole.SUPER_ADMIN
    assert super_data["username"] == "916220518"
    super_headers = {"Authorization": f"Bearer {super_data['access_token']}"}

    # El Super Admin puede gestionar VC3 y también VC2 sin bloqueo cross-tenant
    vc3_lots = await client.get("/api/v1/lots", headers=super_headers)
    assert vc3_lots.status_code == 200

    vc2_lots = await client.get(
        "/api/v1/lots", headers={**super_headers, "X-Villa-Id": "VC2"}
    )
    assert vc2_lots.status_code == 200
    assert vc2_lots.json()["villa_id"] == "VC2"

    # 2. Login del Administrador Oficial de Villa Club 3 (934253226)
    vc3_admin_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "934253226", "password": "93425322"},
    )
    assert vc3_admin_login.status_code == 200
    vc3_admin_data = vc3_admin_login.json()
    assert vc3_admin_data["role"] == UserRole.ADMIN
    vc3_admin_headers = {"Authorization": f"Bearer {vc3_admin_data['access_token']}"}

    # El Admin de VC3 puede gestionar VC3 pero NO puede cruzar a VC2
    vc3_ok = await client.get("/api/v1/lots", headers=vc3_admin_headers)
    assert vc3_ok.status_code == 200

    vc2_denied = await client.get(
        "/api/v1/lots", headers={**vc3_admin_headers, "X-Villa-Id": "VC2"}
    )
    assert vc2_denied.status_code == 403


# ---------------------------------------------------------------------------
# 14. FLUJO 3NF PERSONA-ANCLA (ANCHOR) & HOUSEHOLD: Angel Plasencia + QR Amigo Caillou + 403 Delete
# ---------------------------------------------------------------------------
async def test_anchor_adds_household_angel_plasencia_and_caillou_qr_traceability(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import auth as auth_module

    captured_pins: dict[str, str] = {}

    async def fake_send_whatsapp_pin(
        *,
        phone: str,
        villa_id: str,
        lot_id: str,
        owner_name: str,
        pin_password: str,
    ) -> tuple[bool, str]:
        captured_pins[phone] = pin_password
        return True, "evolution_api_mock"

    monkeypatch.setattr(
        auth_module, "send_whatsapp_pin_notification", fake_send_whatsapp_pin
    )

    # 1. El Admin de Villa 3 (934253226) inicia sesión y precarga el Lote P20 con Persona-Ancla 916220518
    admin_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "934253226", "password": "93425322"},
    )
    vc3_admin_headers = {
        "Authorization": f"Bearer {admin_login.json()['access_token']}"
    }

    await client.put(
        "/api/v1/lots/bulk",
        headers=vc3_admin_headers,
        json={
            "lots": [
                {
                    "lot_id": "P20",
                    "owner_name": "Jesus Plasencia",
                    "owner_phone": "916220518",
                    "is_maintenance_up_to_date": True,
                }
            ]
        },
    )

    # 2. Jesús (916220518 - Persona-Ancla de P20) inicia sesión y agrega a Angel Plasencia (980180219) como HOUSEHOLD
    anchor_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "916220518", "password": "91622051"},
    )
    anchor_headers = {"Authorization": f"Bearer {anchor_login.json()['access_token']}"}

    add_mem_resp = await client.post(
        "/api/v1/lots/P20/members",
        headers=anchor_headers,
        json={
            "phone": "980180219",
            "full_name": "Angel Plasencia",
            "document_id": "71234567",
        },
    )
    assert add_mem_resp.status_code == 201
    mem_data = add_mem_resp.json()
    assert mem_data["villa_id"] == "VC3"
    assert mem_data["lot_id"] == "P20"
    assert mem_data["phone"] == "980180219"
    assert mem_data["full_name"] == "Angel Plasencia"
    assert mem_data["membership_role"] == "HOUSEHOLD"

    # 3. Angel Plasencia (980180219) entra por primera vez -> lookup detecta NEEDS_PHONE_ACTIVATION
    angel_lookup = await client.post(
        "/api/v1/auth/lookup",
        json={"username": "980180219"},
    )
    assert angel_lookup.status_code == 200
    assert angel_lookup.json()["status"] == "NEEDS_PHONE_ACTIVATION"
    assert angel_lookup.json()["membership_role"] == "HOUSEHOLD"
    assert angel_lookup.json()["available_lots"] == ["P20"]

    # 4. Angel activa su PIN por WhatsApp (Evolution API) e inicia sesión con su celular
    angel_act = await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "980180219", "phone": "980180219"},
    )
    assert angel_act.status_code == 200
    angel_pin = captured_pins["51980180219"]

    angel_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "980180219", "password": angel_pin},
    )
    assert angel_login.status_code == 200
    angel_token_data = angel_login.json()
    assert angel_token_data["lot_id"] == "P20"
    assert angel_token_data["membership_role"] == "HOUSEHOLD"
    assert angel_token_data["full_name"] == "Angel Plasencia"
    angel_headers = {"Authorization": f"Bearer {angel_token_data['access_token']}"}

    # 5. Como HOUSEHOLD, Angel genera un QR para la visita de "Amigo Caillou"
    qr_resp = await client.post(
        "/api/v1/passes",
        headers=angel_headers,
        json={"pass_type": "GUEST", "visitor_info": "Amigo Caillou"},
    )
    assert qr_resp.status_code == 201
    qr_data = qr_resp.json()
    assert qr_data["villa_id"] == "VC3"
    assert qr_data["lot_id"] == "P20"
    assert qr_data["visitor_info"] == "Amigo Caillou"
    assert qr_data["created_by_phone"] == "980180219"
    assert qr_data["created_by_name"] == "Angel Plasencia"
    assert qr_data["created_by_role"] == "HOUSEHOLD"

    # 6. REGLA DE PERMISOS: Como HOUSEHOLD, Angel NO puede agregar ni borrar a nadie del lote P20 (403 Forbidden)
    forbidden_add = await client.post(
        "/api/v1/lots/P20/members",
        headers=angel_headers,
        json={"phone": "955111222", "full_name": "Intruso"},
    )
    assert forbidden_add.status_code == 403
    assert "HOUSEHOLD" in forbidden_add.json()["detail"]

    forbidden_del = await client.delete(
        "/api/v1/lots/P20/members/916220518",
        headers=angel_headers,
    )
    assert forbidden_del.status_code == 403

    # 7. En cambio, la Persona-Ancla (916220518) SÍ puede retirar a un HOUSEHOLD (soft-deactivate is_active=False)
    anchor_del = await client.delete(
        "/api/v1/lots/P20/members/980180219",
        headers=anchor_headers,
    )
    assert anchor_del.status_code == 200
    assert anchor_del.json()["is_active"] is False

    # 8. Tras la baja, el JWT viejo de Angel (aún vigente 30 días) YA NO sirve para generar pases
    revoked_qr = await client.post(
        "/api/v1/passes",
        headers=angel_headers,
        json={"pass_type": "GUEST", "visitor_info": "Intento post-baja"},
    )
    assert revoked_qr.status_code == 403

    # ...ni para listar a los integrantes del lote
    revoked_list = await client.get("/api/v1/lots/P20/members", headers=angel_headers)
    assert revoked_list.status_code == 403

    # La lista del lote ya no incluye a Angel (baja lógica, no DELETE)
    members_after = await client.get("/api/v1/lots/P20/members", headers=anchor_headers)
    assert [m["phone"] for m in members_after.json()] == ["916220518"]

    # 9. Angel ya activó su PIN: nadie puede forzar un nuevo reset por /activate-pin (anti-DoS)
    replay = await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "980180219", "phone": "980180219"},
    )
    assert replay.status_code == 409

    # 10. El ANCHOR re-agrega a Angel -> la misma fila se reactiva (upsert), sin duplicados
    readd = await client.post(
        "/api/v1/lots/P20/members",
        headers=anchor_headers,
        json={"phone": "980180219", "full_name": "Angel Plasencia"},
    )
    assert readd.status_code == 201
    assert readd.json()["is_active"] is True
    restored_qr = await client.post(
        "/api/v1/passes",
        headers=angel_headers,
        json={"pass_type": "GUEST", "visitor_info": "Amigo Caillou (2)"},
    )
    assert restored_qr.status_code == 201


# ---------------------------------------------------------------------------
# 15. ESCENARIO 3NF MULTI-LOTE Y ROLES CRUZADOS: Pepito Juárez (P20, Q02) y Pepita Reina (Q03 Anchor + P20 Household)
# ---------------------------------------------------------------------------
async def test_3nf_multi_lot_and_cross_role_pepito_and_pepita(
    client: AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import auth as auth_module

    captured_pins: dict[str, str] = {}

    async def fake_send_whatsapp_pin(
        *,
        phone: str,
        villa_id: str,
        lot_id: str,
        owner_name: str,
        pin_password: str,
    ) -> tuple[bool, str]:
        captured_pins[phone] = pin_password
        return True, "evolution_api_mock"

    monkeypatch.setattr(
        auth_module, "send_whatsapp_pin_notification", fake_send_whatsapp_pin
    )

    # 1. El Admin precarga P20 y Q02 a nombre de Pepito Juárez (911222333) y Q03 a nombre de Pepita Reina (944555666)
    await client.put(
        "/api/v1/lots/bulk",
        headers=admin_headers,
        json={
            "lots": [
                {
                    "lot_id": "P20",
                    "owner_name": "Pepito Juarez",
                    "owner_phone": "911222333",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "Q02",
                    "owner_name": "Pepito Juarez",
                    "owner_phone": "911222333",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "Q03",
                    "owner_name": "Pepita Reina",
                    "owner_phone": "944555666",
                    "is_maintenance_up_to_date": True,
                },
            ]
        },
    )

    # 2. Pepito activa su único PIN e inicia sesión -> ve sus 2 lotes [P20, Q02]
    await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "911222333", "phone": "911222333"},
    )
    pepito_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "911222333", "password": captured_pins["51911222333"]},
    )
    assert pepito_login.status_code == 200
    assert pepito_login.json()["available_lots"] == ["P20", "Q02"]
    pepito_headers = {
        "Authorization": f"Bearer {pepito_login.json()['access_token']}"
    }

    # Pepito agrega a Pepita Reina (944555666) como HOUSEHOLD en P20
    add_pepita = await client.post(
        "/api/v1/lots/P20/members",
        headers=pepito_headers,
        json={"phone": "944555666", "full_name": "Pepita Reina"},
    )
    assert add_pepita.status_code == 201
    assert add_pepita.json()["membership_role"] == "HOUSEHOLD"

    # 3. Pepita Reina activa su PIN e inicia sesión -> es ANCHOR en Q03 y HOUSEHOLD en P20
    await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "944555666", "phone": "944555666"},
    )
    pepita_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "944555666", "password": captured_pins["51944555666"]},
    )
    assert pepita_login.status_code == 200
    pepita_data = pepita_login.json()
    assert pepita_data["available_lots"] == ["Q03", "P20"]
    pepita_headers = {"Authorization": f"Bearer {pepita_data['access_token']}"}

    mems_resp = await client.get(
        "/api/v1/lots/my-memberships", headers=pepita_headers
    )
    assert mems_resp.status_code == 200
    roles_by_lot = {
        m["lot_id"]: m["membership_role"] for m in mems_resp.json()
    }
    assert roles_by_lot == {"Q03": "ANCHOR", "P20": "HOUSEHOLD"}

    # Pepita puede generar un QR eligiendo explícitamente el lote P20 (sale con rol HOUSEHOLD)
    pass_p20 = await client.post(
        "/api/v1/passes",
        headers=pepita_headers,
        json={
            "pass_type": "DELIVERY",
            "lot_id": "P20",
            "visitor_info": "Rappi para P20",
        },
    )
    assert pass_p20.status_code == 201
    assert pass_p20.json()["lot_id"] == "P20"
    assert pass_p20.json()["created_by_role"] == "HOUSEHOLD"

    # Y puede generar otro QR eligiendo su propio lote Q03 (sale con rol ANCHOR)
    pass_q03 = await client.post(
        "/api/v1/passes",
        headers=pepita_headers,
        json={
            "pass_type": "RESIDENT_QR",
            "lot_id": "Q03",
            "visitor_info": "Ingreso Personal Pepita",
        },
    )
    assert pass_q03.status_code == 201
    assert pass_q03.json()["lot_id"] == "Q03"
    assert pass_q03.json()["created_by_role"] == "ANCHOR"


# ---------------------------------------------------------------------------
# 16. VISITANTE EXTERNO (PANADERÍA / BOTICA / CANCHITA) + BITÁCORA DIARIA GARITA Y ADMIN
# ---------------------------------------------------------------------------
async def test_visitor_self_registration_for_bakery_and_daily_log(
    client: AsyncClient,
    guard_headers: dict[str, str],
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import auth as auth_module

    captured_pins: dict[str, str] = {}

    async def fake_send_whatsapp_pin(
        *,
        phone: str,
        villa_id: str,
        lot_id: str,
        owner_name: str,
        pin_password: str,
    ) -> tuple[bool, str]:
        captured_pins[phone] = pin_password
        return True, "evolution_api_mock"

    monkeypatch.setattr(
        auth_module, "send_whatsapp_pin_notification", fake_send_whatsapp_pin
    )

    # 1. Una persona externa llega a la puerta de VC3, escanea el QR hacia vc3.lasvillas.club
    # y coloca su celular (955443322) que no está en el padrón:
    lookup = await client.post(
        "/api/v1/auth/lookup", json={"username": "955443322"}
    )
    assert lookup.status_code == 200
    lookup_data = lookup.json()
    assert lookup_data["status"] == "UNREGISTERED_OFFER_VISITOR"
    assert lookup_data["role"] == "VISITOR"
    # SEGURIDAD: ningún teléfono del Administrador ni de residentes se expone a desconocidos
    assert "934253226" not in lookup.text
    assert "916220518" not in lookup.text
    assert "admin_contact_badge" not in lookup_data
    # SHADOW: catálogo de servicios deshabilitado (ni en lookup ni en endpoint)
    assert lookup_data["available_services"] == []
    shadow = await client.get("/api/v1/auth/villa-services")
    assert shadow.status_code == 404

    # Un tercero no puede "registrarse" sobre el número de una cuenta existente (admin/residente)
    hijack = await client.post(
        "/api/v1/auth/register-visitor",
        json={"phone": "934253226", "full_name": "Impostor", "document_id": "12345678"},
    )
    assert hijack.status_code == 409

    # Ni sobre un número precargado como HOUSEHOLD que aún no activó su PIN
    await client.post(
        "/api/v1/lots/B14/members",
        headers=admin_headers,
        json={"phone": "966000111", "full_name": "Conviviente Pendiente"},
    )
    preloaded = await client.post(
        "/api/v1/auth/register-visitor",
        json={"phone": "966000111", "full_name": "Impostor", "document_id": "12345678"},
    )
    assert preloaded.status_code == 409

    # Documento inválido (letras raras / longitud) es rechazado
    bad_doc = await client.post(
        "/api/v1/auth/register-visitor",
        json={"phone": "955443322", "full_name": "Carlos", "document_id": "4589-123"},
    )
    assert bad_doc.status_code == 400

    # 2. Se registra como VISITOR con Nombre + DNI (8 dígitos)
    reg_resp = await client.post(
        "/api/v1/auth/register-visitor",
        json={
            "phone": "955443322",
            "full_name": "Carlos Mendoza",
            "document_id": "45891230",
        },
    )
    assert reg_resp.status_code == 201
    assert reg_resp.json()["role"] == "VISITOR"
    visitor_pin = captured_pins["51955443322"]
    assert len(visitor_pin) == 8 and visitor_pin.isdigit()

    # Re-registro del mismo número -> 409 (nadie puede resetearle el PIN a otro visitante)
    again = await client.post(
        "/api/v1/auth/register-visitor",
        json={"phone": "955443322", "full_name": "Otro", "document_id": "11111111"},
    )
    assert again.status_code == 409
    assert len(captured_pins) == 1  # no se despachó un segundo PIN

    # 3. Inicia sesión con el PIN recibido por WhatsApp
    vis_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "955443322", "password": visitor_pin},
    )
    assert vis_login.status_code == 200
    assert vis_login.json()["role"] == "VISITOR"
    assert vis_login.json()["lot_id"] is None
    vis_headers = {"Authorization": f"Bearer {vis_login.json()['access_token']}"}

    # Un VISITOR no puede generar pases a nombre de un lote ajeno
    hijack_lot = await client.post(
        "/api/v1/passes",
        headers=vis_headers,
        json={"pass_type": "GUEST", "lot_id": "B14"},
    )
    assert hijack_lot.status_code == 403

    # Ni ver la bitácora diaria ni el padrón
    assert (await client.get("/api/v1/passes/daily-log", headers=vis_headers)).status_code == 403
    assert (await client.get("/api/v1/lots", headers=vis_headers)).status_code == 403

    # Genera su QR de visita (destino genérico mientras el catálogo esté en shadow)
    vis_pass_resp = await client.post(
        "/api/v1/passes",
        headers=vis_headers,
        json={"pass_type": "COMMERCE", "destination": "PANADERIA"},
    )
    assert vis_pass_resp.status_code == 201
    vis_pass = vis_pass_resp.json()
    assert vis_pass["destination"] == "VISITA"
    assert vis_pass["created_by_phone"] == "955443322"
    assert vis_pass["created_by_name"] == "Carlos Mendoza"
    assert vis_pass["created_by_role"] == "VISITOR"

    # 4. Garita verifica el QR completando DNI y placa
    verify_resp = await client.post(
        "/api/v1/passes/verify",
        headers=guard_headers,
        json={
            "code": vis_pass["code"],
            "visitor_dni": "45891230",
            "vehicle_plate": "c1k-789",
        },
    )
    assert verify_resp.status_code == 200
    v = verify_resp.json()
    assert v["authorized"] is True
    assert v["created_by_role"] == "VISITOR"
    assert v["checked_in_plate"] == "C1K-789"

    # 5. Garita y Administración ven exactamente el mismo registro en la bitácora diaria
    for headers in (guard_headers, admin_headers):
        daily = await client.get("/api/v1/passes/daily-log", headers=headers)
        assert daily.status_code == 200
        log_data = daily.json()
        assert log_data["villa_id"] == "VC3"
        assert log_data["total_passes"] == 1
        assert log_data["checked_in_count"] == 1
        assert log_data["active_pending_count"] == 0
        entry = log_data["items"][0]
        assert entry["code"] == vis_pass["code"]
        assert entry["created_by_name"] == "Carlos Mendoza"
        assert entry["checked_in_dni"] == "45891230"
        assert entry["checked_in_plate"] == "C1K-789"
        assert entry["checked_in_by_guard"] == "GUARDIA1"
        assert entry["status"] == "USED"

    # 6. Aislamiento: el guardia de VC2 no ve la bitácora de VC3
    vc2_guard = create_access_token(
        username="GUARDIA1", role=UserRole.GUARD, villa_id="VC2", lot_id=None
    )
    vc2_daily = await client.get(
        "/api/v1/passes/daily-log",
        headers={"Authorization": f"Bearer {vc2_guard}", "X-Villa-Id": "VC2"},
    )
    assert vc2_daily.status_code == 200
    assert vc2_daily.json()["total_passes"] == 0

    # 7. Un residente no tiene acceso a la bitácora diaria
    b14_token = create_access_token(
        username="B14", role=UserRole.RESIDENT, villa_id="VC3", lot_id="B14"
    )
    resident_daily = await client.get(
        "/api/v1/passes/daily-log", headers={"Authorization": f"Bearer {b14_token}"}
    )
    assert resident_daily.status_code == 403


# ---------------------------------------------------------------------------
# 17. UNA SOLA CONTRASEÑA ENTRE VILLAS (VC3 y VC2): Pepito usa su mismo PIN en ambas villas
# ---------------------------------------------------------------------------
async def test_single_global_phone_password_across_multiple_villas(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import auth as auth_module

    captured_pins: dict[str, str] = {}

    async def fake_send_whatsapp_pin(
        *,
        phone: str,
        villa_id: str,
        lot_id: str,
        owner_name: str,
        pin_password: str,
    ) -> tuple[bool, str]:
        captured_pins[phone] = pin_password
        return True, "evolution_api_mock"

    monkeypatch.setattr(
        auth_module, "send_whatsapp_pin_notification", fake_send_whatsapp_pin
    )

    # Super Admin (916220518) registra a Pepito (977888999) como ANCHOR de P20 en VC3 y ANCHOR de M20 en VC2
    super_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "916220518", "password": "91622051"},
    )
    super_headers = {"Authorization": f"Bearer {super_login.json()['access_token']}"}

    await client.put(
        "/api/v1/lots/bulk",
        headers=super_headers,
        json={
            "lots": [
                {
                    "lot_id": "P20",
                    "owner_name": "Pepito MultiVilla",
                    "owner_phone": "977888999",
                    "is_maintenance_up_to_date": True,
                }
            ]
        },
    )
    await client.put(
        "/api/v1/lots/bulk",
        headers={**super_headers, "X-Villa-Id": "VC2"},
        json={
            "lots": [
                {
                    "lot_id": "M20",
                    "owner_name": "Pepito MultiVilla",
                    "owner_phone": "977888999",
                    "is_maintenance_up_to_date": True,
                }
            ]
        },
    )

    # Pepito activa su PIN una sola vez en VC3
    await client.post(
        "/api/v1/auth/activate-pin",
        json={"username": "977888999", "phone": "977888999"},
    )
    single_pin = captured_pins["51977888999"]

    # Pepito entra a vc3.lasvillas.club con su celular y ese único PIN -> obtiene su lote P20
    vc3_resp = await client.post(
        "/api/v1/auth/login",
        headers={"X-Villa-Id": "VC3"},
        json={"username": "977888999", "password": single_pin},
    )
    assert vc3_resp.status_code == 200
    assert vc3_resp.json()["villa_id"] == "VC3"
    assert vc3_resp.json()["lot_id"] == "P20"

    # Y cuando Pepito entra a vc2.lasvillas.club usa EXACTAMENTE el mismo celular y el mismo PIN -> obtiene M20!
    vc2_resp = await client.post(
        "/api/v1/auth/login",
        headers={"X-Villa-Id": "VC2"},
        json={"username": "977888999", "password": single_pin},
    )
    assert vc2_resp.status_code == 200
    assert vc2_resp.json()["villa_id"] == "VC2"
    assert vc2_resp.json()["lot_id"] == "M20"

