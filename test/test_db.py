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
    """Crea un repositorio de usuarios limpio (B14, F08, guardia1, admin) por cada test."""
    return InMemoryUserRepository()


@pytest.fixture
def lot_repo() -> InMemoryLotRepository:
    """Crea un repositorio de lotes limpio (B14 al día, F08 moroso) por cada test."""
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
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def b14_headers() -> dict[str, str]:
    token = create_access_token(username="B14", role=UserRole.RESIDENT, lot_id="B14")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def f08_headers() -> dict[str, str]:
    token = create_access_token(username="F08", role=UserRole.RESIDENT, lot_id="F08")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def guard_headers() -> dict[str, str]:
    token = create_access_token(username="GUARDIA1", role=UserRole.GUARD, lot_id=None)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    token = create_access_token(username="ADMIN", role=UserRole.ADMIN, lot_id=None)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. HAPPY PATH: Login B14 -> Crea pase con 1 dato -> Vigilante verifica con DNI + Placa
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
# 4. REGLA DE MOROSIDAD (403 Forbidden): Vecino F08 con deuda no genera pases
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

    stored_pass = await repo.get_pass_by_code(code)
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

    updated_pass = await repo.get_pass_by_code(code)
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
# 7. ADMIN BULK SYNC: Crea nuevos lotes (U28, P20) y autogenera claves WhatsApp
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
                    "lot_id": "F08",  # Existente (ahora ya pagó)
                    "owner_name": "Vecino Ya Pagó",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "U28",  # Nuevo lote en el padrón
                    "owner_name": "Familia Salgado",
                    "is_maintenance_up_to_date": True,
                },
            ]
        },
    )
    assert bulk_resp.status_code == 200
    data = bulk_resp.json()
    assert data["processed_count"] == 2
    assert data["created_count"] == 1
    assert data["updated_count"] == 1
    assert len(data["generated_credentials"]) == 1

    u28_cred = data["generated_credentials"][0]
    assert u28_cred["lot_id"] == "U28"
    assert u28_cred["initial_password"].startswith("VC3-U28-")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "U28", "password": u28_cred["initial_password"]},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert token_data["expires_in_days"] == 30
    assert token_data["lot_id"] == "U28"


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
    assert reports[0]["ip_address"] == attacker_ip
    assert reports[0]["attack_type"] == AttackType.PASSWORD_SPRAYING
    assert reports[0]["attempted_usernames"] == ["B14", "U28", "P20"]
    assert reports[0]["suspected_lots_from_previous_logins"] == ["F08"]
