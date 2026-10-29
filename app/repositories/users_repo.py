from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.security import hash_password
from app.models.lot import normalize_lot_id, normalize_villa_id
from app.models.user import AttackType, BlockedIPReport, User, UserRole


class UserRepositoryProtocol(Protocol):
    async def get_user(self, villa_id: str, username: str) -> User | None: ...
    async def create_or_get_resident_user(
        self,
        villa_id: str,
        lot_id: str,
        initial_password: str,
        email: str | None = None,
    ) -> tuple[User, bool]: ...
    async def update_password(
        self, villa_id: str, username: str, new_password_hash: str
    ) -> User | None: ...
    async def record_login_success(
        self, villa_id: str, username: str, client_ip: str
    ) -> User | None: ...
    async def record_login_failure(
        self,
        villa_id: str,
        attempted_username: str,
        client_ip: str,
        user_agent: str,
    ) -> BlockedIPReport: ...
    async def is_ip_blocked(
        self, villa_id: str, client_ip: str
    ) -> BlockedIPReport | None: ...
    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]: ...
    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool: ...


class _IPTrackingState:
    def __init__(self, villa_id: str, ip_address: str) -> None:
        self.villa_id = villa_id
        self.ip_address = ip_address
        self.failed_attempts: int = 0
        self.is_blocked: bool = False
        self.attempted_usernames: list[str] = []
        self.blocked_at: datetime | None = None
        self.last_user_agent: str | None = None


def _lazy_hash(password: str) -> str:
    return hash_password(password, salt="vc3seedsalt2026")


class InMemoryUserRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        # Llave compuesta: (villa_id, username)
        # Cada Villa (VC1, VC2, VC3) tiene sus propios vecinos, guardias y junta directiva (admin)
        self._users: dict[tuple[str, str], User] = {
            ("VC3", "B14"): User(
                villa_id="VC3",
                username="B14",
                password_hash=_lazy_hash("VC3-B14"),
                role=UserRole.RESIDENT,
                lot_id="B14",
                email="jesus.b14@lasvillas.club",
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "F08"): User(
                villa_id="VC3",
                username="F08",
                password_hash=_lazy_hash("VC3-F08"),
                role=UserRole.RESIDENT,
                lot_id="F08",
                email="vecino.f08@lasvillas.club",
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "GUARDIA1"): User(
                villa_id="VC3",
                username="GUARDIA1",
                password_hash=_lazy_hash("guardia123"),
                role=UserRole.GUARD,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "ADMIN"): User(
                villa_id="VC3",
                username="ADMIN",
                password_hash=_lazy_hash("admin123"),
                role=UserRole.ADMIN,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
            # Usuarios semilla para Villa Club 2 (vc2.lasvillas.club)
            ("VC2", "B14"): User(
                villa_id="VC2",
                username="B14",
                password_hash=_lazy_hash("VC2-B14"),
                role=UserRole.RESIDENT,
                lot_id="B14",
                email="vecino.vc2.b14@lasvillas.club",
                created_at=now,
                updated_at=now,
            ),
            ("VC2", "GUARDIA1"): User(
                villa_id="VC2",
                username="GUARDIA1",
                password_hash=_lazy_hash("guardia123"),
                role=UserRole.GUARD,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
            ("VC2", "ADMIN"): User(
                villa_id="VC2",
                username="ADMIN",
                password_hash=_lazy_hash("admin123"),
                role=UserRole.ADMIN,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
        }
        # Rastreo de intentos fallidos por (villa_id, ip_address)
        self._ip_tracking: dict[tuple[str, str], _IPTrackingState] = {}
        # Historial de qué lotes han iniciado sesión exitosamente desde cada (villa_id, ip_address)
        self._ip_known_users: dict[tuple[str, str], list[str]] = {}

    def _key(self, villa_id: str, username: str) -> tuple[str, str]:
        return (normalize_villa_id(villa_id), normalize_lot_id(username))

    def _build_report(self, state: _IPTrackingState) -> BlockedIPReport:
        unique_targets = list(dict.fromkeys(state.attempted_usernames))
        attack_type = (
            AttackType.PASSWORD_SPRAYING
            if len(unique_targets) > 1
            else AttackType.TARGETED_BRUTE_FORCE
        )
        suspected_users = self._ip_known_users.get(
            (state.villa_id, state.ip_address), []
        )
        return BlockedIPReport(
            villa_id=state.villa_id,
            ip_address=state.ip_address,
            failed_attempts=state.failed_attempts,
            is_blocked=state.is_blocked,
            attack_type=attack_type,
            attempted_usernames=unique_targets,
            suspected_lots_from_previous_logins=list(suspected_users),
            blocked_at=state.blocked_at,
            last_user_agent=state.last_user_agent,
        )

    async def get_user(self, villa_id: str, username: str) -> User | None:
        return self._users.get(self._key(villa_id, username))

    async def create_or_get_resident_user(
        self,
        villa_id: str,
        lot_id: str,
        initial_password: str,
        email: str | None = None,
    ) -> tuple[User, bool]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        key = (norm_villa, norm_lot)
        existing = self._users.get(key)
        if existing:
            if email:
                existing.email = email
            return existing, False

        now = datetime.now(UTC)
        new_user = User(
            villa_id=norm_villa,
            username=norm_lot,
            password_hash=hash_password(initial_password),
            role=UserRole.RESIDENT,
            lot_id=norm_lot,
            email=email,
            created_at=now,
            updated_at=now,
        )
        self._users[key] = new_user
        return new_user, True

    async def update_password(
        self, villa_id: str, username: str, new_password_hash: str
    ) -> User | None:
        user = self._users.get(self._key(villa_id, username))
        if not user:
            return None
        user.password_hash = new_password_hash
        user.updated_at = datetime.now(UTC)
        return user

    async def record_login_success(
        self, villa_id: str, username: str, client_ip: str
    ) -> User | None:
        norm_villa, norm_username = self._key(villa_id, username)
        user = self._users.get((norm_villa, norm_username))
        if not user:
            return None

        now = datetime.now(UTC)
        user.last_login_at = now
        user.last_login_ip = client_ip
        user.updated_at = now

        ip_key = (norm_villa, client_ip)
        known_list = self._ip_known_users.setdefault(ip_key, [])
        if norm_username not in known_list:
            known_list.append(norm_username)

        if ip_key in self._ip_tracking and not self._ip_tracking[ip_key].is_blocked:
            del self._ip_tracking[ip_key]

        return user

    async def record_login_failure(
        self,
        villa_id: str,
        attempted_username: str,
        client_ip: str,
        user_agent: str,
    ) -> BlockedIPReport:
        norm_villa, norm_target = self._key(villa_id, attempted_username)
        ip_key = (norm_villa, client_ip)
        state = self._ip_tracking.setdefault(
            ip_key, _IPTrackingState(norm_villa, client_ip)
        )
        state.failed_attempts += 1
        state.attempted_usernames.append(norm_target)
        state.last_user_agent = user_agent

        if (
            state.failed_attempts >= settings.max_failed_attempts_per_ip
            and not state.is_blocked
        ):
            state.is_blocked = True
            state.blocked_at = datetime.now(UTC)

        return self._build_report(state)

    async def is_ip_blocked(
        self, villa_id: str, client_ip: str
    ) -> BlockedIPReport | None:
        ip_key = (normalize_villa_id(villa_id), client_ip)
        state = self._ip_tracking.get(ip_key)
        if state and state.is_blocked:
            return self._build_report(state)
        return None

    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]:
        norm_villa = normalize_villa_id(villa_id)
        return [
            self._build_report(state)
            for state in self._ip_tracking.values()
            if state.villa_id == norm_villa and state.is_blocked
        ]

    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool:
        ip_key = (normalize_villa_id(villa_id), client_ip)
        if ip_key in self._ip_tracking:
            del self._ip_tracking[ip_key]
            return True
        return False


in_memory_user_repo = InMemoryUserRepository()
