"""Personas (identidad global por UUID), roles de personal, radar de IPs y solicitudes de PIN."""

import secrets
import uuid
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.security import hash_password
from app.models.lot import normalize_phone_username, normalize_villa_id
from app.models.user import AttackType, BlockedIPReport, Person, StaffRole, StaffRoleName


class PeopleRepositoryProtocol(Protocol):
    async def get_by_id(self, person_id: str) -> Person | None: ...
    async def get_by_phone(self, phone: str) -> Person | None: ...
    async def upsert_person(
        self,
        phone: str,
        full_name: str | None = None,
        document_id: str | None = None,
        *,
        overwrite: bool = False,
    ) -> Person: ...
    async def update_person(
        self, person_id: str, *, full_name: str | None = None, document_id: str | None = None
    ) -> Person | None: ...
    async def change_phone(self, person_id: str, new_phone: str) -> Person | None: ...
    async def set_password(self, person_id: str, password_hash: str) -> None: ...
    async def reset_access(self, person_id: str) -> None: ...
    async def record_login_success(self, person_id: str, villa_id: str, client_ip: str) -> None: ...
    async def get_staff_role(self, villa_id: str, person_id: str) -> StaffRole | None: ...
    async def upsert_staff_role(
        self, villa_id: str, person_id: str, role: StaffRoleName, is_active: bool = True
    ) -> StaffRole: ...
    async def list_staff(self, villa_id: str) -> list[tuple[StaffRole, Person]]: ...
    async def record_login_failure(
        self, villa_id: str, attempted_username: str, client_ip: str, user_agent: str
    ) -> BlockedIPReport: ...
    async def is_ip_blocked(self, villa_id: str, client_ip: str) -> BlockedIPReport | None: ...
    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]: ...
    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool: ...
    async def record_pin_request(self, villa_id: str, person_id: str, client_ip: str, ok: bool) -> None: ...
    async def count_pin_requests(self, villa_id: str, person_id: str, since: datetime) -> int: ...


def norm_phone(raw: str) -> str:
    return normalize_phone_username(raw) or raw.strip()


def build_ip_report(
    villa_id: str,
    ip: str,
    failed: int,
    blocked: bool,
    attempted: list[str],
    known: list[str],
    blocked_at: datetime | None,
    user_agent: str | None,
) -> BlockedIPReport:
    unique_targets = list(dict.fromkeys(attempted))
    return BlockedIPReport(
        villa_id=villa_id,
        ip_address=ip,
        failed_attempts=failed,
        is_blocked=blocked,
        attack_type=AttackType.PASSWORD_SPRAYING if len(unique_targets) > 1 else AttackType.TARGETED_BRUTE_FORCE,
        attempted_usernames=unique_targets,
        suspected_lots_from_previous_logins=list(known),
        blocked_at=blocked_at,
        last_user_agent=user_agent,
    )


class _IPState:
    def __init__(self, villa_id: str, ip: str) -> None:
        self.villa_id = villa_id
        self.ip = ip
        self.failed = 0
        self.blocked = False
        self.attempted: list[str] = []
        self.blocked_at: datetime | None = None
        self.user_agent: str | None = None


class InMemoryPeopleRepository:
    """Repositorio en memoria (desarrollo rápido y tests). Siembra mínima para pruebas locales."""

    def __init__(self, seed: bool = True) -> None:
        self._people: dict[str, Person] = {}
        self._staff: dict[tuple[str, str], StaffRole] = {}
        self._ip: dict[tuple[str, str], _IPState] = {}
        self._known: dict[tuple[str, str], list[str]] = {}
        self.pin_requests: list[tuple[str, str, datetime, str, bool]] = []
        if seed:
            self._seed()

    # ---------- semilla local ----------
    def _seed(self) -> None:
        now = datetime.now(UTC)

        def add(phone: str, name: str, doc: str | None, pin: str | None, *, super_admin: bool = False) -> Person:
            p = Person(
                id=str(uuid.uuid4()),
                phone=phone,
                full_name=name,
                document_id=doc,
                password_hash=hash_password(pin, salt="vc3seedsalt2026") if pin else None,
                is_super_admin=super_admin,
                last_login_at=now if pin else None,
                created_at=now,
                updated_at=now,
            )
            self._people[p.id] = p
            return p

        add("916220518", "Jesus Plasencia", "70000001", "91622051", super_admin=True)
        admin = add("934253226", "Administrador Villa Club 3", "40000001", "93425322")
        guard = add("900000001", "Vigilante de Prueba", "40000003", "90000001")
        guard_vc2 = add("900000002", "Vigilante de Prueba VC2", "40000004", "90000002")
        add("999111222", "Vecino Moroso", "40000005", None)
        add("988777666", "Propietario VC2 B14", "40000006", None)
        for villa, person, role in (
            ("VC3", admin, StaffRoleName.ADMIN),
            ("VC3", guard, StaffRoleName.GUARD),
            ("VC2", guard_vc2, StaffRoleName.GUARD),
        ):
            self._staff[(villa, person.id)] = StaffRole(
                villa_id=villa, person_id=person.id, role=role, created_at=now, updated_at=now
            )

    def ensure_person(
        self, phone: str, full_name: str | None = None, document_id: str | None = None, pin: str | None = None
    ) -> Person:
        """Ayuda síncrona (tests / desarrollo): devuelve o crea la persona; si se da `pin`, queda activada."""
        target = norm_phone(phone)
        p = next((x for x in self._people.values() if x.phone == target), None)
        now = datetime.now(UTC)
        if not p:
            p = Person(id=str(uuid.uuid4()), phone=target, full_name=full_name, document_id=document_id, created_at=now, updated_at=now)
            self._people[p.id] = p
        if pin:
            p.password_hash = hash_password(pin)
            p.last_login_at = now
        return p

    # ---------- personas ----------
    async def get_by_id(self, person_id: str) -> Person | None:
        return self._people.get(str(person_id))

    async def get_by_phone(self, phone: str) -> Person | None:
        target = norm_phone(phone)
        return next((p for p in self._people.values() if p.phone == target), None)

    async def upsert_person(
        self,
        phone: str,
        full_name: str | None = None,
        document_id: str | None = None,
        *,
        overwrite: bool = False,
    ) -> Person:
        now = datetime.now(UTC)
        existing = await self.get_by_phone(phone)
        if existing:
            if full_name and (overwrite or not existing.full_name):
                existing.full_name = full_name
            if document_id and (overwrite or not existing.document_id):
                existing.document_id = document_id
            existing.updated_at = now
            return existing
        p = Person(
            id=str(uuid.uuid4()),
            phone=norm_phone(phone),
            full_name=full_name,
            document_id=document_id,
            created_at=now,
            updated_at=now,
        )
        self._people[p.id] = p
        return p

    async def update_person(
        self, person_id: str, *, full_name: str | None = None, document_id: str | None = None
    ) -> Person | None:
        p = self._people.get(str(person_id))
        if not p:
            return None
        if full_name:
            p.full_name = full_name
        if document_id:
            p.document_id = document_id
        p.updated_at = datetime.now(UTC)
        return p

    async def change_phone(self, person_id: str, new_phone: str) -> Person | None:
        p = self._people.get(str(person_id))
        if not p:
            return None
        p.phone = norm_phone(new_phone)
        p.updated_at = datetime.now(UTC)
        return p

    async def set_password(self, person_id: str, password_hash: str) -> None:
        p = self._people[str(person_id)]
        p.password_hash = password_hash
        p.updated_at = datetime.now(UTC)

    async def reset_access(self, person_id: str) -> None:
        """Invalida la contraseña, cierra sesiones (token_version++) y reinicia el límite de PIN."""
        p = self._people[str(person_id)]
        now = datetime.now(UTC)
        p.password_hash = hash_password(secrets.token_urlsafe(24))
        p.last_login_at = None
        p.token_version += 1
        p.access_reset_at = now
        p.updated_at = now

    async def record_login_success(self, person_id: str, villa_id: str, client_ip: str) -> None:
        p = self._people[str(person_id)]
        now = datetime.now(UTC)
        p.last_login_at = now
        p.last_login_ip = client_ip
        p.updated_at = now
        key = (normalize_villa_id(villa_id), client_ip)
        known = self._known.setdefault(key, [])
        if p.phone not in known:
            known.append(p.phone)
        state = self._ip.get(key)
        if state and not state.blocked:
            del self._ip[key]

    # ---------- personal ----------
    async def get_staff_role(self, villa_id: str, person_id: str) -> StaffRole | None:
        r = self._staff.get((normalize_villa_id(villa_id), str(person_id)))
        return r if r and r.is_active else None

    async def upsert_staff_role(
        self, villa_id: str, person_id: str, role: StaffRoleName, is_active: bool = True
    ) -> StaffRole:
        key = (normalize_villa_id(villa_id), str(person_id))
        now = datetime.now(UTC)
        current = self._staff.get(key)
        if current:
            current.role = role
            current.is_active = is_active
            current.updated_at = now
            return current
        r = StaffRole(villa_id=key[0], person_id=key[1], role=role, is_active=is_active, created_at=now, updated_at=now)
        self._staff[key] = r
        return r

    async def list_staff(self, villa_id: str) -> list[tuple[StaffRole, Person]]:
        norm = normalize_villa_id(villa_id)
        return [(r, self._people[r.person_id]) for (v, _), r in self._staff.items() if v == norm]

    # ---------- radar de IPs ----------
    def _report(self, s: _IPState) -> BlockedIPReport:
        return build_ip_report(
            s.villa_id, s.ip, s.failed, s.blocked, s.attempted, self._known.get((s.villa_id, s.ip), []), s.blocked_at, s.user_agent
        )

    async def record_login_failure(
        self, villa_id: str, attempted_username: str, client_ip: str, user_agent: str
    ) -> BlockedIPReport:
        key = (normalize_villa_id(villa_id), client_ip)
        s = self._ip.setdefault(key, _IPState(*key))
        s.failed += 1
        s.attempted.append(norm_phone(attempted_username))
        s.user_agent = user_agent
        if s.failed >= settings.max_failed_attempts_per_ip and not s.blocked:
            s.blocked = True
            s.blocked_at = datetime.now(UTC)
        return self._report(s)

    async def is_ip_blocked(self, villa_id: str, client_ip: str) -> BlockedIPReport | None:
        s = self._ip.get((normalize_villa_id(villa_id), client_ip))
        return self._report(s) if s and s.blocked else None

    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]:
        norm = normalize_villa_id(villa_id)
        return [self._report(s) for s in self._ip.values() if s.villa_id == norm and s.blocked]

    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool:
        s = self._ip.get((normalize_villa_id(villa_id), client_ip))
        if not s or not s.blocked:
            return False
        del self._ip[(s.villa_id, s.ip)]
        return True

    # ---------- solicitudes de PIN ----------
    async def record_pin_request(self, villa_id: str, person_id: str, client_ip: str, ok: bool) -> None:
        self.pin_requests.append((normalize_villa_id(villa_id), str(person_id), datetime.now(UTC), client_ip, ok))

    async def count_pin_requests(self, villa_id: str, person_id: str, since: datetime) -> int:
        norm = normalize_villa_id(villa_id)
        return sum(1 for v, pid, at, _, ok in self.pin_requests if v == norm and pid == str(person_id) and ok and at >= since)


in_memory_people_repo = InMemoryPeopleRepository()
