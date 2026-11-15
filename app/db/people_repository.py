import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import IPSecurityRecordORM, PersonORM, PinRequestORM, StaffRoleORM
from app.models.lot import normalize_villa_id
from app.models.user import BlockedIPReport, Person, StaffRole, StaffRoleName
from app.repositories.people_repo import build_ip_report, norm_phone


def person_to_domain(row: PersonORM) -> Person:
    return Person(
        id=str(row.id),
        phone=row.phone,
        full_name=row.full_name,
        document_id=row.document_id,
        password_hash=row.password_hash,
        is_super_admin=row.is_super_admin,
        is_active=row.is_active,
        token_version=row.token_version or 0,
        access_reset_at=row.access_reset_at,
        last_login_at=row.last_login_at,
        last_login_ip=row.last_login_ip,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class PostgresPeopleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _row_by_phone(self, phone: str) -> PersonORM | None:
        stmt = select(PersonORM).where(PersonORM.phone == norm_phone(phone))
        return (await self.session.execute(stmt)).scalars().first()

    async def _row(self, person_id: str) -> PersonORM | None:
        pid = _uuid(person_id)
        return await self.session.get(PersonORM, pid) if pid else None

    async def get_by_id(self, person_id: str) -> Person | None:
        row = await self._row(person_id)
        return person_to_domain(row) if row else None

    async def get_by_phone(self, phone: str) -> Person | None:
        row = await self._row_by_phone(phone)
        return person_to_domain(row) if row else None

    async def upsert_person(
        self,
        phone: str,
        full_name: str | None = None,
        document_id: str | None = None,
        *,
        overwrite: bool = False,
    ) -> Person:
        now = datetime.now(UTC)
        row = await self._row_by_phone(phone)
        if row:
            if full_name and (overwrite or not row.full_name):
                row.full_name = full_name
            if document_id and (overwrite or not row.document_id):
                row.document_id = document_id
            row.updated_at = now
        else:
            row = PersonORM(
                id=uuid.uuid4(),
                phone=norm_phone(phone),
                full_name=full_name,
                document_id=document_id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        await self.session.flush()
        return person_to_domain(row)

    async def update_person(
        self, person_id: str, *, full_name: str | None = None, document_id: str | None = None
    ) -> Person | None:
        row = await self._row(person_id)
        if not row:
            return None
        if full_name:
            row.full_name = full_name
        if document_id:
            row.document_id = document_id
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return person_to_domain(row)

    async def set_password(self, person_id: str, password_hash: str) -> None:
        row = await self._row(person_id)
        if row:
            row.password_hash = password_hash
            row.updated_at = datetime.now(UTC)
            await self.session.flush()

    async def reset_access(self, person_id: str) -> None:
        row = await self._row(person_id)
        if not row:
            return
        now = datetime.now(UTC)
        row.password_hash = hash_password(secrets.token_urlsafe(24))
        row.last_login_at = None
        row.token_version = (row.token_version or 0) + 1
        row.access_reset_at = now
        row.updated_at = now
        await self.session.flush()

    async def record_login_success(self, person_id: str, villa_id: str, client_ip: str) -> None:
        row = await self._row(person_id)
        if not row:
            return
        now = datetime.now(UTC)
        row.last_login_at = now
        row.last_login_ip = client_ip
        row.updated_at = now
        norm = normalize_villa_id(villa_id)
        ip_row = await self.session.get(IPSecurityRecordORM, (norm, client_ip))
        if not ip_row:
            self.session.add(
                IPSecurityRecordORM(
                    villa_id=norm,
                    ip_address=client_ip,
                    failed_attempts=0,
                    is_blocked=False,
                    attempted_usernames=[],
                    known_successful_users=[row.phone],
                )
            )
        else:
            known = list(ip_row.known_successful_users or [])
            if row.phone not in known:
                ip_row.known_successful_users = [*known, row.phone]
            if not ip_row.is_blocked:
                ip_row.failed_attempts = 0
                ip_row.attempted_usernames = []
        await self.session.flush()

    # ---------- personal ----------
    @staticmethod
    def _staff_domain(row: StaffRoleORM) -> StaffRole:
        return StaffRole(
            villa_id=row.villa_id,
            person_id=str(row.person_id),
            role=StaffRoleName(row.role),
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_staff_role(self, villa_id: str, person_id: str) -> StaffRole | None:
        pid = _uuid(person_id)
        if not pid:
            return None
        row = await self.session.get(StaffRoleORM, (normalize_villa_id(villa_id), pid))
        return self._staff_domain(row) if row and row.is_active else None

    async def upsert_staff_role(
        self, villa_id: str, person_id: str, role: StaffRoleName, is_active: bool = True
    ) -> StaffRole:
        key = (normalize_villa_id(villa_id), uuid.UUID(str(person_id)))
        now = datetime.now(UTC)
        row = await self.session.get(StaffRoleORM, key)
        if row:
            row.role = str(role)
            row.is_active = is_active
            row.updated_at = now
        else:
            row = StaffRoleORM(
                villa_id=key[0], person_id=key[1], role=str(role), is_active=is_active, created_at=now, updated_at=now
            )
            self.session.add(row)
        await self.session.flush()
        return self._staff_domain(row)

    async def list_staff(self, villa_id: str) -> list[tuple[StaffRole, Person]]:
        stmt = (
            select(StaffRoleORM, PersonORM)
            .join(PersonORM, PersonORM.id == StaffRoleORM.person_id)
            .where(StaffRoleORM.villa_id == normalize_villa_id(villa_id))
            .order_by(StaffRoleORM.role, PersonORM.full_name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(self._staff_domain(s), person_to_domain(p)) for s, p in rows]

    # ---------- radar de IPs ----------
    @staticmethod
    def _report(row: IPSecurityRecordORM) -> BlockedIPReport:
        return build_ip_report(
            row.villa_id,
            row.ip_address,
            row.failed_attempts,
            row.is_blocked,
            list(row.attempted_usernames or []),
            list(row.known_successful_users or []),
            row.blocked_at,
            row.last_user_agent,
        )

    async def record_login_failure(
        self, villa_id: str, attempted_username: str, client_ip: str, user_agent: str
    ) -> BlockedIPReport:
        norm = normalize_villa_id(villa_id)
        row = await self.session.get(IPSecurityRecordORM, (norm, client_ip))
        if not row:
            row = IPSecurityRecordORM(
                villa_id=norm,
                ip_address=client_ip,
                failed_attempts=0,
                is_blocked=False,
                attempted_usernames=[],
                known_successful_users=[],
            )
            self.session.add(row)
        row.failed_attempts += 1
        row.attempted_usernames = [*(row.attempted_usernames or []), norm_phone(attempted_username)]
        row.last_user_agent = user_agent
        if row.failed_attempts >= settings.max_failed_attempts_per_ip and not row.is_blocked:
            row.is_blocked = True
            row.blocked_at = datetime.now(UTC)
        await self.session.flush()
        return self._report(row)

    async def is_ip_blocked(self, villa_id: str, client_ip: str) -> BlockedIPReport | None:
        row = await self.session.get(IPSecurityRecordORM, (normalize_villa_id(villa_id), client_ip))
        return self._report(row) if row and row.is_blocked else None

    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]:
        stmt = select(IPSecurityRecordORM).where(
            IPSecurityRecordORM.villa_id == normalize_villa_id(villa_id),
            IPSecurityRecordORM.is_blocked.is_(True),
        )
        return [self._report(r) for r in (await self.session.execute(stmt)).scalars().all()]

    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool:
        row = await self.session.get(IPSecurityRecordORM, (normalize_villa_id(villa_id), client_ip))
        if not row or not row.is_blocked:
            return False
        row.is_blocked = False
        row.failed_attempts = 0
        row.attempted_usernames = []
        row.blocked_at = None
        await self.session.flush()
        return True

    # ---------- solicitudes de PIN ----------
    async def record_pin_request(self, villa_id: str, person_id: str, client_ip: str, ok: bool) -> None:
        self.session.add(
            PinRequestORM(
                villa_id=normalize_villa_id(villa_id),
                person_id=uuid.UUID(str(person_id)),
                client_ip=client_ip,
                ok=ok,
                requested_at=datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def count_pin_requests(self, villa_id: str, person_id: str, since: datetime) -> int:
        stmt = select(func.count()).select_from(PinRequestORM).where(
            PinRequestORM.villa_id == normalize_villa_id(villa_id),
            PinRequestORM.person_id == uuid.UUID(str(person_id)),
            PinRequestORM.ok.is_(True),
            PinRequestORM.requested_at >= since,
        )
        return int((await self.session.execute(stmt)).scalar() or 0)
