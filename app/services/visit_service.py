import secrets
import string
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from app.core.audit import log_audit_event
from app.core.config import settings
from app.core.pii import mask_doc, normalize_plate, pii_hmac
from app.core.security import AuthenticatedIdentity
from app.models.lot import LotType, PropertyLot, normalize_lot_id, normalize_villa_id
from app.models.user import UserRole
from app.models.visit import (
    AccessSearchResponse,
    AddAttendeesRequest,
    Attendee,
    AttendeeCheckIn,
    AttendeeInput,
    CatalogBulkRequest,
    CatalogBulkResponse,
    CatalogItem,
    CheckInRequest,
    CreateInviteRequest,
    CreatePublicVisitRequest,
    DocType,
    GuardAttendeeView,
    GuardDeskVisitRequest,
    GuardPassView,
    InviteResponse,
    PublicPassView,
    PublicVillaResponse,
    Verification,
    VisitKind,
    VisitLogResponse,
    VisitOrigin,
    VisitPass,
    VisitStatus,
)
from app.repositories.visit_repo import VisitRepositoryProtocol
from app.services.entry_service import EntryService

_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1")


class PublicRateLimiter:
    """Límite simple por IP+Villa para el cartel público (por proceso)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: int = 3600) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


public_rate_limiter = PublicRateLimiter()


def _label(lot: PropertyLot) -> str:
    if lot.lot_type == LotType.LOT:
        return f"Lote {lot.lot_id}"
    return lot.display_name or lot.lot_id


class VisitService:
    def __init__(
        self, repo: VisitRepositoryProtocol, limiter: PublicRateLimiter | None = None
    ) -> None:
        self.repo = repo
        self.limiter = limiter or public_rate_limiter

    # ---------- helpers ----------
    def _short_code(self) -> str:
        return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))

    def _new_attendee(self, no: int, person: AttendeeInput, now: datetime) -> Attendee:
        return Attendee(
            attendee_no=no,
            first_names=person.first_names,
            last_names=person.last_names,
            doc_type=person.doc_type or DocType.NINGUNO,
            doc_number=person.doc_number,
            doc_hmac=pii_hmac(person.doc_number),
            doc_last2=person.doc_number[-2:] if person.doc_number else None,
            created_at=now,
        )

    async def _new_visit(
        self,
        villa_id: str,
        origin: VisitOrigin,
        lot: PropertyLot,
        *,
        valid_from: datetime,
        valid_until: datetime,
        party_size: int,
        host: tuple[str | None, str | None, str | None] = (None, None, None),
        note: str | None = None,
        plate: str | None = None,
        kind: VisitKind = VisitKind.GUEST,
    ) -> VisitPass:
        code = self._short_code()
        while await self.repo.get_visit_by_code(villa_id, code):
            code = self._short_code()
        norm_plate = normalize_plate(plate)
        return VisitPass(
            villa_id=villa_id,
            token=secrets.token_urlsafe(18),
            short_code=code,
            origin=origin,
            kind=kind,
            destination_lot_id=lot.lot_id,
            destination_type=lot.lot_type,
            destination_label=_label(lot),
            host_phone=host[0],
            host_name=host[1],
            host_role=host[2],
            party_size_expected=party_size,
            note=note,
            vehicle_plate=norm_plate,
            plate_hmac=pii_hmac(norm_plate),
            created_at=datetime.now(UTC),
            valid_from=valid_from,
            valid_until=valid_until,
        )

    def _append_attendees(self, visit: VisitPass, people: list[AttendeeInput]) -> None:
        if len(visit.attendees) + len(people) > settings.visit_max_attendees:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Máximo {settings.visit_max_attendees} personas por pase.",
            )
        now = datetime.now(UTC)
        start = len(visit.attendees) + 1
        for i, p in enumerate(people):
            visit.attendees.append(self._new_attendee(start + i, p, now))

    async def _public_destination(self, villa_id: str, lot_id: str) -> PropertyLot:
        lot = await self.repo.get_lot(villa_id, lot_id)
        if not lot or not lot.is_public or lot.lot_type == LotType.LOT:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Ese destino no está en el catálogo público de la Villa.",
            )
        return lot

    def _refresh_status(self, visit: VisitPass, now: datetime) -> None:
        if visit.status == VisitStatus.ACTIVE and now > visit.valid_until:
            visit.status = VisitStatus.EXPIRED

    def _guard_view(
        self, visit: VisitPass, authorized: bool, message: str, reveal: bool = True
    ) -> GuardPassView:
        return GuardPassView(
            authorized=authorized,
            message=message,
            short_code=visit.short_code,
            origin=visit.origin,
            kind=visit.kind,
            destination_label=visit.destination_label,
            host_name=visit.host_name,
            host_role=visit.host_role,
            party_size_expected=visit.party_size_expected,
            status=visit.status,
            created_at=visit.created_at,
            valid_until=visit.valid_until,
            vehicle_plate=visit.vehicle_plate,
            checked_in_at=visit.checked_in_at,
            checked_in_by_guard=visit.checked_in_by_guard,
            attendees=[
                GuardAttendeeView(
                    attendee_no=a.attendee_no,
                    full_name=a.full_name,
                    doc_type=a.doc_type,
                    doc_display=mask_doc(a.doc_number if reveal else None, a.doc_last2),
                    verification=a.verification,
                    vouched_by_no=a.vouched_by_no,
                    checked_in_at=a.checked_in_at,
                )
                for a in visit.attendees
            ],
        )

    # ---------- catálogo (vitrina) ----------
    async def public_villa(self, villa_id: str) -> PublicVillaResponse:
        norm = normalize_villa_id(villa_id)
        lots = await self.repo.list_catalog(norm)
        return PublicVillaResponse(
            villa_id=norm,
            catalog=[
                CatalogItem(lot_id=lot.lot_id, lot_type=lot.lot_type, display_name=_label(lot))
                for lot in lots
            ],
        )

    async def bulk_catalog(
        self, villa_id: str, payload: CatalogBulkRequest, admin: AuthenticatedIdentity
    ) -> CatalogBulkResponse:
        norm = normalize_villa_id(villa_id)
        if any(i.lot_type == LotType.LOT for i in payload.items):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "El catálogo solo admite MINI_LOT o COMMON_AREA; los lotes residenciales van por /lots/bulk.",
            )
        created = updated = 0
        items: list[CatalogItem] = []
        for item in payload.items:
            existing = await self.repo.get_lot(norm, item.lot_id)
            if existing and existing.lot_type == LotType.LOT:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{existing.lot_id} es un lote residencial; no se puede convertir en catálogo.",
                )
            lot, was_created = await self.repo.upsert_catalog_item(norm, item)
            created += was_created
            updated += not was_created
            items.append(CatalogItem(lot_id=lot.lot_id, lot_type=lot.lot_type, display_name=_label(lot)))
        log_audit_event(
            event="CATALOG_BULK_UPSERT",
            actor=f"{norm}:{admin.username}",
            client_ip="internal",
            user_agent="api",
            details={"villa_id": norm, "created": created, "updated": updated},
        )
        return CatalogBulkResponse(
            villa_id=norm, created_count=created, updated_count=updated, items=items
        )

    # ---------- invitación del residente ----------
    async def create_invite(
        self, villa_id: str, payload: CreateInviteRequest, identity: AuthenticatedIdentity
    ) -> InviteResponse:
        norm = normalize_villa_id(villa_id)
        is_admin = identity.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
        lot_id = normalize_lot_id(payload.lot_id) if payload.lot_id else None
        membership = None
        if lot_id:
            membership = await self.repo.get_membership(norm, lot_id, identity.username)
        else:
            mems = await self.repo.list_memberships_by_phone(norm, identity.username)
            membership = mems[0] if mems else None
            lot_id = membership.lot_id if membership else identity.lot_id
        if not lot_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Indica el lote al que invitas.")
        legacy_lot_login = identity.lot_id == lot_id and not identity.username.isdigit()
        if not membership and not is_admin and not legacy_lot_login:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"No tienes membresía activa (ANCHOR ni HOUSEHOLD) sobre el lote '{lot_id}'.",
            )
        lot = await self.repo.get_lot(norm, lot_id)
        if not lot or lot.lot_type != LotType.LOT:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"El lote '{lot_id}' no existe en {norm}.")
        if not lot.is_maintenance_up_to_date:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Lote {lot.lot_id} registra deuda de mantenimiento; no puede emitir invitaciones automáticas.",
            )
        now = datetime.now(UTC)
        valid_from = payload.valid_from or now
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=UTC)
        if valid_from < now - timedelta(minutes=5) or valid_from > now + timedelta(days=30):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Fecha de inicio inválida.")
        if payload.kind == VisitKind.TAXI:
            duration = timedelta(minutes=settings.taxi_pass_ttl_minutes)
        elif payload.kind == VisitKind.DELIVERY:
            duration = timedelta(minutes=settings.delivery_pass_ttl_minutes)
        else:
            duration = timedelta(hours=min(payload.valid_hours, settings.visit_invite_max_hours))
        visit = await self._new_visit(
            norm,
            VisitOrigin.HOST_INVITE,
            lot,
            valid_from=valid_from,
            valid_until=valid_from + duration,
            party_size=max(payload.party_size_expected, len(payload.attendees)),
            host=(
                membership.phone if membership else identity.username,
                membership.full_name if membership else (identity.full_name or lot.owner_name),
                str(membership.membership_role) if membership else str(identity.role),
            ),
            note=payload.note,
            kind=payload.kind,
        )
        self._append_attendees(visit, payload.attendees)
        await self.repo.save_visit(visit)
        log_audit_event(
            event="VISIT_INVITE_CREATED",
            actor=f"{norm}:{identity.username}",
            client_ip="internal",
            user_agent="api",
            details={"villa_id": norm, "code": visit.short_code, "lot_id": lot.lot_id,
                     "party_size": visit.party_size_expected},
        )
        return InviteResponse(
            token=visit.token,
            short_code=visit.short_code,
            kind=visit.kind,
            villa_id=norm,
            destination_label=visit.destination_label,
            party_size_expected=visit.party_size_expected,
            valid_from=visit.valid_from,
            valid_until=visit.valid_until,
            share_path=f"/v/{visit.token}",
        )

    # ---------- cartel público de garita ----------
    async def create_public_visit(
        self, villa_id: str, payload: CreatePublicVisitRequest, client_ip: str
    ) -> InviteResponse:
        norm = normalize_villa_id(villa_id)
        if not self.limiter.check(f"{norm}:{client_ip}", settings.public_visits_per_ip_per_hour):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiados pases desde esta conexión. Acércate a la garita.",
            )
        lot = await self._public_destination(norm, payload.destination_lot_id)
        now = datetime.now(UTC)
        visit = await self._new_visit(
            norm,
            VisitOrigin.PUBLIC_KIOSK,
            lot,
            valid_from=now,
            valid_until=now + timedelta(hours=settings.visit_public_ttl_hours),
            party_size=len(payload.attendees),
            plate=payload.vehicle_plate,
        )
        self._append_attendees(visit, payload.attendees)
        await self.repo.save_visit(visit)
        log_audit_event(
            event="VISIT_PUBLIC_CREATED",
            actor=f"{norm}:PUBLIC",
            client_ip=client_ip,
            user_agent="kiosk",
            details={"villa_id": norm, "code": visit.short_code, "destination": lot.lot_id,
                     "party_size": len(visit.attendees)},
        )
        return InviteResponse(
            token=visit.token,
            short_code=visit.short_code,
            villa_id=norm,
            destination_label=visit.destination_label,
            party_size_expected=visit.party_size_expected,
            valid_from=visit.valid_from,
            valid_until=visit.valid_until,
            share_path=f"/v/{visit.token}",
        )

    async def _get_or_404(self, villa_id: str, token: str) -> VisitPass:
        visit = await self.repo.get_visit(normalize_villa_id(villa_id), token)
        if not visit:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Pase no encontrado.")
        return visit

    async def public_view(self, villa_id: str, token: str) -> PublicPassView:
        visit = await self._get_or_404(villa_id, token)
        self._refresh_status(visit, datetime.now(UTC))
        return PublicPassView(
            short_code=visit.short_code,
            villa_id=visit.villa_id,
            destination_label=visit.destination_label,
            status=visit.status,
            valid_from=visit.valid_from,
            valid_until=visit.valid_until,
            party_size_expected=visit.party_size_expected,
            attendee_names=[a.full_name for a in visit.attendees],
        )

    async def add_attendees(
        self, villa_id: str, token: str, payload: AddAttendeesRequest
    ) -> PublicPassView:
        visit = await self._get_or_404(villa_id, token)
        self._refresh_status(visit, datetime.now(UTC))
        if visit.status != VisitStatus.ACTIVE:
            raise HTTPException(status.HTTP_409_CONFLICT, "El pase ya no está activo.")
        self._append_attendees(visit, payload.attendees)
        visit.party_size_expected = max(visit.party_size_expected, len(visit.attendees))
        await self.repo.save_visit(visit)
        return await self.public_view(villa_id, token)

    # ---------- garita ----------
    async def guard_lookup(self, villa_id: str, ref: str) -> GuardPassView:
        norm = normalize_villa_id(villa_id)
        visit = await self.repo.get_visit(norm, ref) or await self.repo.get_visit_by_code(norm, ref)
        if not visit:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"El pase no existe en {norm}.")
        now = datetime.now(UTC)
        self._refresh_status(visit, now)
        ok, msg = self._can_enter(visit, now)
        return self._guard_view(visit, ok, msg)

    def _can_enter(self, visit: VisitPass, now: datetime) -> tuple[bool, str]:
        if visit.status == VisitStatus.USED:
            return False, "DENEGADO: este pase ya fue usado."
        if visit.status == VisitStatus.EXPIRED:
            return False, "DENEGADO: el pase venció."
        if now < visit.valid_from - timedelta(minutes=30):
            return False, "AÚN NO: el pase todavía no está vigente."
        return True, f"AUTORIZADO: hacia {visit.destination_label}."

    def _apply_checkins(
        self, visit: VisitPass, entries: list[AttendeeCheckIn], guard: str, now: datetime
    ) -> None:
        by_no = {a.attendee_no: a for a in visit.attendees}
        for entry in entries:
            if entry.attendee_no is not None:
                att = by_no.get(entry.attendee_no)
                if not att:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        f"El asistente #{entry.attendee_no} no está en el pase.",
                    )
                if entry.person:
                    fresh = self._new_attendee(att.attendee_no, entry.person, att.created_at)
                    att.first_names, att.last_names = fresh.first_names, fresh.last_names
                    att.doc_type, att.doc_number = fresh.doc_type, fresh.doc_number
                    att.doc_hmac, att.doc_last2 = fresh.doc_hmac, fresh.doc_last2
            else:
                assert entry.person is not None
                self._append_attendees(visit, [entry.person])
                att = visit.attendees[-1]
                by_no[att.attendee_no] = att
            verification = entry.verification or (
                Verification.VOUCHED
                if entry.vouched_by_no
                else Verification.DOC_SHOWN
                if att.doc_number
                else Verification.DECLARED
            )
            if verification in (Verification.DOC_SHOWN, Verification.DOC_DIGITAL) and not att.doc_number:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"{att.full_name}: {verification} requiere número de documento.",
                )
            att.verification = verification
            att.vouched_by_no = entry.vouched_by_no
            att.checked_in_at = now
            att.checked_in_by_guard = guard
        # Validar avales al final (el acompañante puede venir después en la lista)
        for att in visit.attendees:
            if att.verification == Verification.VOUCHED:
                voucher = by_no.get(att.vouched_by_no or -1)
                if not voucher or voucher.attendee_no == att.attendee_no or not voucher.checked_in_at:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        f"{att.full_name}: el aval debe ser otro asistente que ingresa en este grupo.",
                    )

    async def check_in(
        self, villa_id: str, ref: str, payload: CheckInRequest, guard: AuthenticatedIdentity
    ) -> GuardPassView:
        norm = normalize_villa_id(villa_id)
        visit = await self.repo.get_visit(norm, ref) or await self.repo.get_visit_by_code(norm, ref)
        if not visit:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"El pase no existe en {norm}.")
        now = datetime.now(UTC)
        self._refresh_status(visit, now)
        ok, msg = self._can_enter(visit, now)
        if not ok:
            if visit.status == VisitStatus.EXPIRED:
                await self.repo.save_visit(visit)
            raise HTTPException(status.HTTP_409_CONFLICT, msg)
        if visit.destination_type == LotType.LOT:
            lot = await self.repo.get_lot(norm, visit.destination_lot_id)
            if not lot or not lot.is_maintenance_up_to_date:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "DENEGADO: el lote ya no cuenta con habilitación de mantenimiento.",
                )
        plate = normalize_plate(payload.vehicle_plate) or visit.vehicle_plate
        if visit.kind in (VisitKind.TAXI, VisitKind.DELIVERY) and not plate:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"FALTA PLACA: para {visit.kind} es obligatorio registrar la placa antes de abrir.",
            )
        self._apply_checkins(visit, payload.attendees, guard.username, now)
        if payload.vehicle_plate:
            visit.vehicle_plate = normalize_plate(payload.vehicle_plate)
            visit.plate_hmac = pii_hmac(visit.vehicle_plate)
        visit.status = VisitStatus.USED
        visit.checked_in_at = now
        visit.checked_in_by_guard = guard.username
        await self.repo.save_visit(visit)
        people_in = sum(1 for a in visit.attendees if a.checked_in_at)
        log_audit_event(
            event="VISIT_CHECKED_IN",
            actor=f"{norm}:{guard.username}",
            client_ip="internal",
            user_agent="gate",
            details={"villa_id": norm, "code": visit.short_code,
                     "destination": visit.destination_lot_id, "people_in": people_in},
        )
        return self._guard_view(visit, True, f"INGRESARON {people_in} persona(s) hacia {visit.destination_label}.")

    async def guard_desk_visit(
        self, villa_id: str, payload: GuardDeskVisitRequest, guard: AuthenticatedIdentity
    ) -> GuardPassView:
        """El vigilante registra en su tablet a quien no tiene celular (mismo formulario)."""
        norm = normalize_villa_id(villa_id)
        lot = await self.repo.get_lot(norm, payload.destination_lot_id)
        if not lot:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Destino no encontrado en la Villa.")
        if any(e.attendee_no is not None for e in payload.attendees):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "En mesa de garita todas las personas se ingresan con sus datos (person).",
            )
        now = datetime.now(UTC)
        visit = await self._new_visit(
            norm,
            VisitOrigin.GUARD_DESK,
            lot,
            valid_from=now,
            valid_until=now + timedelta(hours=settings.visit_public_ttl_hours),
            party_size=len(payload.attendees),
            host=(None, None, "GUARD"),
        )
        await self.repo.save_visit(visit)
        return await self.check_in(
            norm,
            visit.token,
            CheckInRequest(attendees=payload.attendees, vehicle_plate=payload.vehicle_plate),
            guard,
        )

    async def daily_log(
        self,
        villa_id: str,
        limit: int = 200,
        *,
        reveal: bool = False,
        actor: AuthenticatedIdentity | None = None,
        client_ip: str = "internal",
    ) -> VisitLogResponse:
        """Bitácora 24h. DNIs enmascarados salvo reveal=True, que queda auditado."""
        norm = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        since = now - timedelta(hours=24)
        visits = await self.repo.search_visits(norm, date_from=since, limit=limit)
        views = []
        for v in visits:
            self._refresh_status(v, now)
            views.append(
                self._guard_view(v, v.status == VisitStatus.ACTIVE, str(v.status), reveal=reveal)
            )
        residents = await EntryService(self.repo).search(
            norm, lot_id=None, plate=None, date_from=since, date_to=None, limit=limit
        )
        if reveal and actor:
            await self.repo.log_data_access(
                {
                    "villa_id": norm,
                    "actor": actor.username,
                    "actor_role": str(actor.role),
                    "action": "DAILY_LOG_REVEAL",
                    "filters": {"hours": 24},
                    "result_count": len(visits),
                    "client_ip": client_ip,
                    "created_at": now,
                }
            )
        return VisitLogResponse(
            villa_id=norm,
            total_passes=len(visits),
            checked_in_passes=sum(1 for v in visits if v.checked_in_at),
            total_people_in=sum(1 for v in visits for a in v.attendees if a.checked_in_at),
            resident_entries=len(residents),
            docs_revealed=reveal,
            items=views,
            resident_items=residents,
        )

    # ---------- admin: búsqueda de incidentes ----------
    async def access_search(
        self,
        villa_id: str,
        admin: AuthenticatedIdentity,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        dni: str | None,
        plate: str | None,
        destination_lot_id: str | None,
        client_ip: str,
        limit: int = 200,
    ) -> AccessSearchResponse:
        from app.core.pii import normalize_doc

        norm = normalize_villa_id(villa_id)
        if not any([date_from, date_to, dni, plate, destination_lot_id]):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Indica al menos un filtro (fechas, DNI, placa o destino).",
            )
        norm_dest = normalize_lot_id(destination_lot_id) if destination_lot_id else None
        visits = await self.repo.search_visits(
            norm,
            date_from=date_from,
            date_to=date_to,
            doc_hmac=pii_hmac(normalize_doc(dni)),
            plate_hmac=pii_hmac(normalize_plate(plate)),
            destination_lot_id=norm_dest,
            only_checked_in=True,
            limit=limit,
        )
        resident_entries = (
            []
            if dni
            else await EntryService(self.repo).search(
                norm,
                lot_id=norm_dest,
                plate=plate,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        )
        await self.repo.log_data_access(
            {
                "villa_id": norm,
                "actor": admin.username,
                "actor_role": str(admin.role),
                "action": "ACCESS_SEARCH",
                "filters": {
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "dni": bool(dni),
                    "plate": bool(plate),
                    "destination": norm_dest,
                },
                "result_count": len(visits) + len(resident_entries),
                "client_ip": client_ip,
                "created_at": datetime.now(UTC),
            }
        )
        return AccessSearchResponse(
            villa_id=norm,
            result_count=len(visits),
            items=[self._guard_view(v, False, "HISTORIAL") for v in visits],
            resident_entries=resident_entries,
        )

    async def shadow_old_pii(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.visitor_pii_retention_days)
        return await self.repo.shadow_pii_before(cutoff)
