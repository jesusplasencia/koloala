from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.v1.lots import get_lots_service
from app.api.v1.passes import get_access_service
from app.main import app
from app.models.access import PassStatus, PassType
from app.repositories.access_repo import InMemoryAccessRepository
from app.repositories.lots_repo import InMemoryLotRepository
from app.services.access_service import AccessService
from app.services.lots_service import LotsService


@pytest.fixture
def lot_repo() -> InMemoryLotRepository:
    """Crea un repositorio de lotes limpio (con datos semilla) para cada test."""
    return InMemoryLotRepository()


@pytest.fixture
def repo(lot_repo: InMemoryLotRepository) -> InMemoryAccessRepository:
    """Crea un repositorio de accesos conectado al mismo padrón de lotes (lot_repo)."""
    return InMemoryAccessRepository(lots_store=lot_repo.lots_store)


@pytest.fixture
def client(repo: InMemoryAccessRepository, lot_repo: InMemoryLotRepository):
    """
    Inyecta ambos repositorios compartiendo el mismo estado de lotes en memoria.
    Así lo que el administrador edite en /api/v1/lots impacta de inmediato en /api/v1/passes.
    """
    app.dependency_overrides[get_access_service] = lambda: AccessService(repo=repo)
    app.dependency_overrides[get_lots_service] = lambda: LotsService(repo=lot_repo)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. HAPPY PATH: Residente al día crea pase -> Vigilante verifica y abre reja
# ---------------------------------------------------------------------------
def test_create_and_verify_pass_happy_path(client: TestClient) -> None:
    create_resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-B-L14",
            "pass_type": "DELIVERY",
            "visitor_info": "Rappi - Moto ABC-123",
        },
    )
    assert create_resp.status_code == 201
    pass_data = create_resp.json()
    assert len(pass_data["code"]) == 6
    assert pass_data["status"] == PassStatus.ACTIVE

    verify_resp = client.post(
        "/api/v1/passes/verify",
        json={"code": pass_data["code"]},
    )
    assert verify_resp.status_code == 200
    verification = verify_resp.json()
    assert verification["authorized"] is True
    assert verification["lot_id"] == "MZ-B-L14"
    assert verification["owner_name"] == "Jesus Plasencia"


# ---------------------------------------------------------------------------
# 2. REGLA ANTI-REUSO (Single-Use Pass): Un código quemado no puede reusarse
# ---------------------------------------------------------------------------
def test_cannot_reuse_already_used_pass(client: TestClient) -> None:
    create_resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-B-L14",
            "pass_type": "TAXI",
            "visitor_info": "Uber Placa XYZ-999",
        },
    )
    code = create_resp.json()["code"]

    first_verify = client.post("/api/v1/passes/verify", json={"code": code})
    assert first_verify.json()["authorized"] is True

    second_verify = client.post("/api/v1/passes/verify", json={"code": code})
    assert second_verify.status_code == 200
    assert second_verify.json()["authorized"] is False
    assert "ya fue utilizado" in second_verify.json()["message"]


# ---------------------------------------------------------------------------
# 3. REGLA DE MOROSIDAD (403 Forbidden): Vecino con deuda no genera pases
# ---------------------------------------------------------------------------
def test_delinquent_lot_cannot_create_pass(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-F-L08",  # Vecino Moroso en datos semilla
            "pass_type": "DELIVERY",
            "visitor_info": "PedidosYa - Pollería",
        },
    )
    assert resp.status_code == 403
    assert "deuda de mantenimiento" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. REGLA DE PADRÓN (404 Not Found): Lote inexistente en Villa Club 3
# ---------------------------------------------------------------------------
def test_nonexistent_lot_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-Z-L99",
            "pass_type": "GUEST",
            "visitor_info": "Visita Familiar",
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. REGLA DE EXPIRACIÓN (TTL Vencido): Taxi llega tarde a la garita
# ---------------------------------------------------------------------------
async def test_expired_pass_is_denied_at_gatehouse(
    client: TestClient, repo: InMemoryAccessRepository
) -> None:
    create_resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-B-L14",
            "pass_type": PassType.TAXI,
            "visitor_info": "Taxi Indriver",
        },
    )
    code = create_resp.json()["code"]

    stored_pass = await repo.get_pass_by_code(code)
    assert stored_pass is not None
    stored_pass.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await repo.save_pass(stored_pass)

    verify_resp = client.post("/api/v1/passes/verify", json={"code": code})
    assert verify_resp.json()["authorized"] is False
    assert "expirado" in verify_resp.json()["message"]

    updated_pass = await repo.get_pass_by_code(code)
    assert updated_pass is not None
    assert updated_pass.status == PassStatus.EXPIRED


# ---------------------------------------------------------------------------
# 6. REGLA DE REVOCACIÓN EN GARITA (Vía Admin API): Vecino cae en mora
# ---------------------------------------------------------------------------
def test_pass_denied_if_admin_marks_lot_delinquent_before_arrival(
    client: TestClient,
) -> None:
    # 1. A las 9:00 AM el vecino está al día y crea un pase de invitado (8h TTL)
    create_resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-B-L14",
            "pass_type": PassType.GUEST,
            "visitor_info": "Invitado: Carlos Mendoza",
        },
    )
    code = create_resp.json()["code"]

    # 2. A las 11:00 AM el Administrador desde su UI pasa el lote a moroso vía PATCH
    patch_resp = client.patch(
        "/api/v1/lots/MZ-B-L14/maintenance",
        json={"is_maintenance_up_to_date": False},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_maintenance_up_to_date"] is False

    # 3. A las 12:00 PM llega el invitado a garita: denegado automáticamente
    verify_resp = client.post("/api/v1/passes/verify", json={"code": code})
    assert verify_resp.json()["authorized"] is False
    assert "habilitación de mantenimiento" in verify_resp.json()["message"]


# ---------------------------------------------------------------------------
# 7. ADMIN BULK SYNC: Cierre mensual actualiza existentes y crea nuevos lotes
# ---------------------------------------------------------------------------
def test_admin_bulk_upsert_lots(client: TestClient) -> None:
    bulk_resp = client.put(
        "/api/v1/lots/bulk",
        json={
            "lots": [
                {
                    "lot_id": "MZ-F-L08",  # Existente (antes moroso, ahora pagó)
                    "owner_name": "Vecino Ya Pagó",
                    "is_maintenance_up_to_date": True,
                },
                {
                    "lot_id": "MZ-C-L22",  # Lote nuevo en el padrón
                    "owner_name": "Nuevo Propietario",
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

    # Ahora MZ-F-L08 (que estaba bloqueado) ya puede pedir su Delivery sin 403
    pass_resp = client.post(
        "/api/v1/passes",
        json={
            "lot_id": "MZ-F-L08",
            "pass_type": "DELIVERY",
            "visitor_info": "Rappi - Farmacia",
        },
    )
    assert pass_resp.status_code == 201
