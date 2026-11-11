from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.security import hash_password
from app.models.lot import normalize_lot_id, normalize_villa_id
from app.models.user import AttackType, BlockedIPReport, User, UserRole


class UserRepositoryProtocol(Protocol):
    async def get_user(self, villa_id: str, username: str) -> User | None: ...
    async def get_user_any_villa(self, username: str) -> User | None: ...
    async def create_or_get_resident_user(
        self,
        villa_id: str,
        lot_id: str,
        initial_password: str,
        email: str | None = None,
        username: str | None = None,
        full_name: str | None = None,
        document_id: str | None = None,
        role: UserRole = UserRole.RESIDENT,
    ) -> tuple[User, bool]: ...
    async def upsert_user_account(
        self,
        villa_id: str,
        username: str,
        password_hash: str,
        role: UserRole,
        lot_id: str | None = None,
        full_name: str | None = None,
        document_id: str | None = None,
        email: str | None = None,
        clear_last_login: bool = False,
    ) -> User: ...
    async def update_password(
        self,
        villa_id: str,
        username: str,
        new_password_hash: str,
        *,
        clear_last_login: bool = False,
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
        # Incluye:
        # - 916220518: Super Admin de la App (Jesús Plasencia) + ANCHOR de P20 en VC3
        # - 934253226: Administrador Oficial de Villa Club 3 (VC3)
        self._users: dict[tuple[str, str], User] = {
            ("VC3", "916220518"): User(
                villa_id="VC3",
                username="916220518",
                password_hash=_lazy_hash("91622051"),
                role=UserRole.SUPER_ADMIN,
                lot_id="P20",
                full_name="Jesus Plasencia",
                document_id="70000001",
                email="jesus.p20@lasvillas.club",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "934253226"): User(
                villa_id="VC3",
                username="934253226",
                password_hash=_lazy_hash("93425322"),
                role=UserRole.ADMIN,
                lot_id=None,
                full_name="Administrador Villa Club 3",
                document_id="40000001",
                email="admin.vc3@lasvillas.club",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "B14"): User(
                villa_id="VC3",
                username="B14",
                password_hash=_lazy_hash("VC3-B14"),
                role=UserRole.RESIDENT,
                lot_id="B14",
                full_name="Jesus Plasencia",
                email="jesus.b14@lasvillas.club",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "F08"): User(
                villa_id="VC3",
                username="F08",
                password_hash=_lazy_hash("VC3-F08"),
                role=UserRole.RESIDENT,
                lot_id="F08",
                full_name="Vecino Moroso",
                email="vecino.f08@lasvillas.club",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "GUARDIA1"): User(
                villa_id="VC3",
                username="GUARDIA1",
                password_hash=_lazy_hash("guardia123"),
                role=UserRole.GUARD,
                lot_id=None,
                full_name="Vigilante Garita VC3",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC3", "ADMIN"): User(
                villa_id="VC3",
                username="ADMIN",
                password_hash=_lazy_hash("admin123"),
                role=UserRole.ADMIN,
                lot_id=None,
                full_name="Administrador VC3",
                last_login_at=now,
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
                full_name="Propietario VC2 B14",
                email="vecino.vc2.b14@lasvillas.club",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC2", "GUARDIA1"): User(
                villa_id="VC2",
                username="GUARDIA1",
                password_hash=_lazy_hash("guardia123"),
                role=UserRole.GUARD,
                lot_id=None,
                full_name="Vigilante Garita VC2",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
            ("VC2", "ADMIN"): User(
                villa_id="VC2",
                username="ADMIN",
                password_hash=_lazy_hash("admin123"),
                role=UserRole.ADMIN,
                lot_id=None,
                full_name="Administrador VC2",
                last_login_at=now,
                created_at=now,
                updated_at=now,
            ),
        }
        # Rastreo de intentos fallidos por (villa_id, ip_address)
        self._ip_tracking: dict[tuple[str, str], _IPTrackingState] = {}
        # Historial de qué lotes/teléfonos han iniciado sesión exitosamente desde cada (villa_id, ip_address)
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
        norm_villa, norm_user = self._key(villa_id, username)
        direct = self._users.get((norm_villa, norm_user))
        if direct:
            return direct
        # Identidad Global Telefónica: si el usuario es un teléfono de 9 dígitos que ya tiene
        # su PIN registrado en otra Villa (ej. VC3) o es SUPER_ADMIN, reutiliza su única contraseña!
        if norm_user.isdigit() and len(norm_user) == 9:
            global_user = await self.get_user_any_villa(norm_user)
            if global_user:
                return global_user
        return None

    async def get_user_any_villa(self, username: str) -> User | None:
        norm_user = normalize_lot_id(username)
        matches = [
            u for u in self._users.values() if u.username == norm_user and u.is_active
        ]
        if not matches:
            return None
        # Priorizar cuenta que ya tenga last_login_at (PIN ya activado) o SUPER_ADMIN
        matches.sort(
            key=lambda u: (
                0 if u.role == UserRole.SUPER_ADMIN else 1,
                0 if u.last_login_at is not None else 1,
            )
        )
        return matches[0]

    async def create_or_get_resident_user(
        self,
        villa_id: str,
        lot_id: str,
        initial_password: str,
        email: str | None = None,
        username: str | None = None,
        full_name: str | None = None,
        document_id: str | None = None,
        role: UserRole = UserRole.RESIDENT,
    ) -> tuple[User, bool]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id) if lot_id else None
        actual_username = normalize_lot_id(username) if username else (norm_lot or "")
        key = (norm_villa, actual_username)
        existing = self._users.get(key)
        if existing:
            if email:
                existing.email = email
            if full_name:
                existing.full_name = full_name
            if document_id:
                existing.document_id = document_id
            return existing, False

        now = datetime.now(UTC)
        new_user = User(
            villa_id=norm_villa,
            username=actual_username,
            password_hash=hash_password(initial_password),
            role=role,
            lot_id=norm_lot,
            full_name=full_name,
            document_id=document_id,
            email=email,
            created_at=now,
            updated_at=now,
        )
        self._users[key] = new_user
        return new_user, True

    async def upsert_user_account(
        self,
        villa_id: str,
        username: str,
        password_hash: str,
        role: UserRole,
        lot_id: str | None = None,
        full_name: str | None = None,
        document_id: str | None = None,
        email: str | None = None,
        clear_last_login: bool = False,
    ) -> User:
        norm_villa, norm_user = self._key(villa_id, username)
        norm_lot = normalize_lot_id(lot_id) if lot_id else None
        now = datetime.now(UTC)
        existing = self._users.get((norm_villa, norm_user))
        if existing:
            existing.password_hash = password_hash
            # No degradar a SUPER_ADMIN o ADMIN si se le agrega un lote como residente
            if existing.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
                existing.role = role
            if norm_lot is not None:
                existing.lot_id = norm_lot
            if full_name is not None:
                existing.full_name = full_name
            if document_id is not None:
                existing.document_id = document_id
            if email is not None:
                existing.email = email
            if clear_last_login:
                existing.last_login_at = None
            existing.updated_at = now
            # Propagar el mismo password_hash a cualquier otra fila con el mismo celular
            if norm_user.isdigit() and len(norm_user) == 9:
                for u in self._users.values():
                    if u.username == norm_user:
                        u.password_hash = password_hash
            return existing

        new_user = User(
            villa_id=norm_villa,
            username=norm_user,
            password_hash=password_hash,
            role=role,
            lot_id=norm_lot,
            full_name=full_name,
            document_id=document_id,
            email=email,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        self._users[(norm_villa, norm_user)] = new_user
        return new_user

    async def update_password(
        self,
        villa_id: str,
        username: str,
        new_password_hash: str,
        *,
        clear_last_login: bool = False,
    ) -> User | None:
        norm_villa, norm_user = self._key(villa_id, username)
        user = self._users.get((norm_villa, norm_user))
        if not user and norm_user.isdigit() and len(norm_user) == 9:
            user = await self.get_user_any_villa(norm_user)
        if not user:
            return None

        now = datetime.now(UTC)
        user.password_hash = new_password_hash
        if clear_last_login:
            user.last_login_at = None
        user.updated_at = now

        # Si es un teléfono de 9 dígitos, mantener sincronizada su única contraseña en todas las villas
        if norm_user.isdigit() and len(norm_user) == 9:
            for u in self._users.values():
                if u.username == norm_user:
                    u.password_hash = new_password_hash
                    if clear_last_login:
                        u.last_login_at = None
                    u.updated_at = now
        return user

    async def record_login_success(
        self, villa_id: str, username: str, client_ip: str
    ) -> User | None:
        norm_villa, norm_username = self._key(villa_id, username)
        user = self._users.get((norm_villa, norm_username))
        if not user and norm_username.isdigit() and len(norm_username) == 9:
            user = await self.get_user_any_villa(norm_username)
        if not user:
            return None

        now = datetime.now(UTC)
        user.last_login_at = now
        user.last_login_ip = client_ip
        user.updated_at = now

        if norm_username.isdigit() and len(norm_username) == 9:
            for u in self._users.values():
                if u.username == norm_username:
                    u.last_login_at = now
                    u.last_login_ip = client_ip

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
        state = self._ip_tracking.get(ip_key)
        if not state or not state.is_blocked:
            return False
        state.is_blocked = False
        state.failed_attempts = 0
        state.attempted_usernames = []
        state.blocked_at = None
        return True


in_memory_user_repo = InMemoryUserRepository()
