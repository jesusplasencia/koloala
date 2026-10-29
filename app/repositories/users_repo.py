from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.security import hash_password
from app.models.lot import normalize_lot_id
from app.models.user import AttackType, BlockedIPReport, User, UserRole


class UserRepositoryProtocol(Protocol):
    async def get_user(self, username: str) -> User | None: ...
    async def create_or_get_resident_user(
        self, lot_id: str, initial_password: str
    ) -> tuple[User, bool]: ...
    async def update_password(self, username: str, new_password_hash: str) -> User | None: ...
    async def record_login_success(self, username: str, client_ip: str) -> User | None: ...
    async def record_login_failure(
        self, attempted_username: str, client_ip: str, user_agent: str
    ) -> BlockedIPReport: ...
    async def is_ip_blocked(self, client_ip: str) -> BlockedIPReport | None: ...
    async def list_blocked_ips(self) -> list[BlockedIPReport]: ...
    async def unblock_ip(self, client_ip: str) -> bool: ...


class _IPTrackingState:
    def __init__(self, ip_address: str) -> None:
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
        # Usuarios semilla de Villa Club 3:
        # - B14 (Residente al día, clave por defecto: VC3-B14)
        # - F08 (Residente moroso, clave por defecto: VC3-F08)
        # - guardia1 (Vigilante de garita, sin lote: clave guardia123)
        # - admin (Administrador de la Villa, sin lote: clave admin123)
        self._users: dict[str, User] = {
            "B14": User(
                username="B14",
                password_hash=_lazy_hash("VC3-B14"),
                role=UserRole.RESIDENT,
                lot_id="B14",
                created_at=now,
                updated_at=now,
            ),
            "F08": User(
                username="F08",
                password_hash=_lazy_hash("VC3-F08"),
                role=UserRole.RESIDENT,
                lot_id="F08",
                created_at=now,
                updated_at=now,
            ),
            "GUARDIA1": User(
                username="GUARDIA1",
                password_hash=_lazy_hash("guardia123"),
                role=UserRole.GUARD,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
            "ADMIN": User(
                username="ADMIN",
                password_hash=_lazy_hash("admin123"),
                role=UserRole.ADMIN,
                lot_id=None,
                created_at=now,
                updated_at=now,
            ),
        }
        # Rastreo de intentos fallidos por IP (Anti-Brute Force + Anti-Password Spraying)
        self._ip_tracking: dict[str, _IPTrackingState] = {}
        # Historial de qué lotes/usuarios han iniciado sesión exitosamente desde cada IP
        self._ip_known_users: dict[str, list[str]] = {}

    def _normalize_username(self, username: str) -> str:
        return normalize_lot_id(username)

    def _build_report(self, state: _IPTrackingState) -> BlockedIPReport:
        unique_targets = list(dict.fromkeys(state.attempted_usernames))
        attack_type = (
            AttackType.PASSWORD_SPRAYING
            if len(unique_targets) > 1
            else AttackType.TARGETED_BRUTE_FORCE
        )
        suspected_users = self._ip_known_users.get(state.ip_address, [])
        return BlockedIPReport(
            ip_address=state.ip_address,
            failed_attempts=state.failed_attempts,
            is_blocked=state.is_blocked,
            attack_type=attack_type,
            attempted_usernames=unique_targets,
            suspected_lots_from_previous_logins=list(suspected_users),
            blocked_at=state.blocked_at,
            last_user_agent=state.last_user_agent,
        )

    async def get_user(self, username: str) -> User | None:
        return self._users.get(self._normalize_username(username))

    async def create_or_get_resident_user(
        self, lot_id: str, initial_password: str
    ) -> tuple[User, bool]:
        norm_id = normalize_lot_id(lot_id)
        existing = self._users.get(norm_id)
        if existing:
            return existing, False

        now = datetime.now(UTC)
        new_user = User(
            username=norm_id,
            password_hash=hash_password(initial_password),
            role=UserRole.RESIDENT,
            lot_id=norm_id,
            created_at=now,
            updated_at=now,
        )
        self._users[norm_id] = new_user
        return new_user, True

    async def update_password(self, username: str, new_password_hash: str) -> User | None:
        user = self._users.get(self._normalize_username(username))
        if not user:
            return None
        user.password_hash = new_password_hash
        user.updated_at = datetime.now(UTC)
        return user

    async def record_login_success(self, username: str, client_ip: str) -> User | None:
        norm_username = self._normalize_username(username)
        user = self._users.get(norm_username)
        if not user:
            return None

        now = datetime.now(UTC)
        user.last_login_at = now
        user.last_login_ip = client_ip
        user.updated_at = now

        # Guardamos correlación: sabemos que desde esta IP entró este usuario con éxito
        known_list = self._ip_known_users.setdefault(client_ip, [])
        if norm_username not in known_list:
            known_list.append(norm_username)

        # Si la IP tenía 1 o 2 fallos por error de dedo, al entrar bien limpiamos su contador
        if client_ip in self._ip_tracking and not self._ip_tracking[client_ip].is_blocked:
            del self._ip_tracking[client_ip]

        return user

    async def record_login_failure(
        self, attempted_username: str, client_ip: str, user_agent: str
    ) -> BlockedIPReport:
        norm_target = self._normalize_username(attempted_username)
        state = self._ip_tracking.setdefault(client_ip, _IPTrackingState(client_ip))
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

    async def is_ip_blocked(self, client_ip: str) -> BlockedIPReport | None:
        state = self._ip_tracking.get(client_ip)
        if state and state.is_blocked:
            return self._build_report(state)
        return None

    async def list_blocked_ips(self) -> list[BlockedIPReport]:
        return [
            self._build_report(state)
            for state in self._ip_tracking.values()
            if state.is_blocked
        ]

    async def unblock_ip(self, client_ip: str) -> bool:
        if client_ip in self._ip_tracking:
            del self._ip_tracking[client_ip]
            return True
        return False


in_memory_user_repo = InMemoryUserRepository()
