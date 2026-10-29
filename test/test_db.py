from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.auth import get_user_repo
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
    """Crea un repositorio de accesos conectado al mismo padrón de lotes (lot_repo)."""
    return InMemoryAccessRepository(lots_store=lot_repo.lots_store)


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
# 7. ADMIN BULK SYNC: Crea nuevos lotes con email (VC3+P20) y autogenera claves
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
                    "owner_email": "f08@lasvillas.club",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "P20",
                    "owner_name": "Familia Torres",
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
    assert p20_cred["initial_password"].startswith("VC3-P20-")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "P20", "password": p20_cred["initial_password"]},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert token_data["expires_in_days"] == 30
    assert token_data["villa_id"] == "VC3"
    assert token_data["lot_id"] == "P20"


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
