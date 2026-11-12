"""Pases grupales sin cuenta: invitación de residente, cartel público (vitrina) y garita."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.audit import get_client_ip
from app.core.config import settings
from app.core.security import AdminDep, GuardOrAdminDep, ResidentIdentityDep, VillaIdDep
from app.db.session import AsyncSessionLocal
from app.db.visit_repository import PostgresVisitRepository
from app.models.entry import (
    CarnetResponse,
    MyEntriesResponse,
    ResidentEntryRequest,
    ResidentEntryView,
)
from app.models.visit import (
    AccessSearchResponse,
    AddAttendeesRequest,
    CatalogBulkRequest,
    CatalogBulkResponse,
    CheckInRequest,
    CreateInviteRequest,
    CreatePublicVisitRequest,
    GuardDeskVisitRequest,
    GuardPassView,
    InviteResponse,
    PublicPassView,
    PublicVillaResponse,
    VisitLogResponse,
)
from app.repositories.visit_repo import in_memory_visit_repo
from app.services.entry_service import EntryService
from app.services.visit_service import VisitService

router = APIRouter(tags=["Group Visits (QR sin cuenta)"])


async def get_visit_service() -> AsyncIterator[VisitService]:
    if not settings.use_postgres:
        yield VisitService(repo=in_memory_visit_repo)
        return
    async with AsyncSessionLocal() as session:
        try:
            yield VisitService(repo=PostgresVisitRepository(session))
            await session.commit()
        except Exception:
            await session.rollback()
            raise


VisitServiceDep = Annotated[VisitService, Depends(get_visit_service)]


# ---------- Público (sin login) ----------
@router.get("/public/villa", response_model=PublicVillaResponse)
async def public_villa_catalog(villa_id: VillaIdDep, service: VisitServiceDep) -> PublicVillaResponse:
    """Vitrina de la Villa (cartel QR de garita): MiniLotes y áreas comunes. Nunca expone datos de residentes."""
    return await service.public_villa(villa_id)


@router.post("/public/visits", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_public_visit(
    payload: CreatePublicVisitRequest,
    request: Request,
    villa_id: VillaIdDep,
    service: VisitServiceDep,
) -> InviteResponse:
    """El visitante elige destino del catálogo y registra a su grupo (DNI opcional). Sin cuenta ni celular."""
    return await service.create_public_visit(villa_id, payload, get_client_ip(request))


@router.get("/public/visits/{token}", response_model=PublicPassView)
async def view_public_visit(token: str, villa_id: VillaIdDep, service: VisitServiceDep) -> PublicPassView:
    return await service.public_view(villa_id, token)


@router.post("/public/visits/{token}/attendees", response_model=PublicPassView)
async def add_visit_attendees(
    token: str, payload: AddAttendeesRequest, villa_id: VillaIdDep, service: VisitServiceDep
) -> PublicPassView:
    """Quien recibió el enlace por WhatsApp completa los nombres de su grupo antes de llegar."""
    return await service.add_attendees(villa_id, token, payload)


# ---------- Residente ----------
@router.post("/visits/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: CreateInviteRequest, resident: ResidentIdentityDep, service: VisitServiceDep
) -> InviteResponse:
    """ANCHOR/HOUSEHOLD invita a un grupo a su lote. No se piden celulares de los invitados."""
    return await service.create_invite(resident.villa_id, payload, resident)


# ---------- Garita ----------
@router.get("/me/carnet", response_model=CarnetResponse)
async def my_carnet(resident: ResidentIdentityDep, service: VisitServiceDep) -> CarnetResponse:
    """Carnet QR del residente: rota cada 30 s. Requiere membresía activa (ANCHOR/HOUSEHOLD)."""
    return await EntryService(service.repo).carnet(resident)


@router.get("/me/entries", response_model=MyEntriesResponse)
async def my_entries(resident: ResidentIdentityDep, service: VisitServiceDep) -> MyEntriesResponse:
    """Historial propio de ingresos a la Villa."""
    return await EntryService(service.repo).my_entries(resident)


@router.post(
    "/guard/resident-entry", response_model=ResidentEntryView, status_code=status.HTTP_201_CREATED
)
async def guard_resident_entry(
    payload: ResidentEntryRequest, guard: GuardOrAdminDep, service: VisitServiceDep
) -> ResidentEntryView:
    """Escanea el carnet (o registro MANUAL por lote + celular dictado). El carnet no se gasta."""
    return await EntryService(service.repo).register_entry(guard, payload)


@router.get("/guard/visits/daily-log", response_model=VisitLogResponse)
async def guard_daily_log(
    request: Request,
    guard: GuardOrAdminDep,
    service: VisitServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    reveal: bool = False,
) -> VisitLogResponse:
    """Últimas 24h (visitas + residentes). DNIs enmascarados salvo reveal=true (auditado)."""
    return await service.daily_log(
        guard.villa_id, limit=limit, reveal=reveal, actor=guard, client_ip=get_client_ip(request)
    )


@router.post("/guard/visits/desk", response_model=GuardPassView, status_code=status.HTTP_201_CREATED)
async def guard_desk_visit(
    payload: GuardDeskVisitRequest, guard: GuardOrAdminDep, service: VisitServiceDep
) -> GuardPassView:
    """El vigilante registra e ingresa a quien no tiene celular, con el mismo formulario."""
    return await service.guard_desk_visit(guard.villa_id, payload, guard)


@router.get("/guard/visits/{ref}", response_model=GuardPassView)
async def guard_lookup(ref: str, guard: GuardOrAdminDep, service: VisitServiceDep) -> GuardPassView:
    """Escaneo del QR (token) o código de 6 caracteres digitado."""
    return await service.guard_lookup(guard.villa_id, ref)


@router.post("/guard/visits/{ref}/check-in", response_model=GuardPassView)
async def guard_check_in(
    ref: str, payload: CheckInRequest, guard: GuardOrAdminDep, service: VisitServiceDep
) -> GuardPassView:
    """Marca a cada asistente; quien no trae DNI ingresa como DECLARED o VOUCHED (avalado)."""
    return await service.check_in(guard.villa_id, ref, payload, guard)


# ---------- Admin ----------
@router.post("/admin/catalog/bulk", response_model=CatalogBulkResponse)
async def admin_catalog_bulk(
    payload: CatalogBulkRequest, admin: AdminDep, service: VisitServiceDep
) -> CatalogBulkResponse:
    """Carga masiva de MiniLotes (A01 - Panadería) y áreas comunes. Upsert idempotente, sin DELETE."""
    return await service.bulk_catalog(admin.villa_id, payload, admin)


@router.get("/admin/access-search", response_model=AccessSearchResponse)
async def admin_access_search(
    request: Request,
    admin: AdminDep,
    service: VisitServiceDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    dni: Annotated[str | None, Query(max_length=20)] = None,
    plate: Annotated[str | None, Query(max_length=15)] = None,
    destination: Annotated[str | None, Query(max_length=15)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> AccessSearchResponse:
    """Búsqueda de incidentes (quién entró, a dónde, con qué placa). Cada consulta queda en data_access_log."""
    return await service.access_search(
        admin.villa_id,
        admin,
        date_from=date_from,
        date_to=date_to,
        dni=dni,
        plate=plate,
        destination_lot_id=destination,
        client_ip=get_client_ip(request),
        limit=limit,
    )
