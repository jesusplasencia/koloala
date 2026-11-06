from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    GuestPassORM,
    IPSecurityRecordORM,
    PropertyLotORM,
    UserORM,
)
from app.models.access import GuestPass, PassStatus, PassType
from app.models.lot import (
    BulkLotItem,
    PropertyLot,
    normalize_lot_id,
    normalize_villa_id,
)
from app.models.user import AttackType, BlockedIPReport, User, UserRole


class PostgresLotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, row: PropertyLotORM) -> PropertyLot:
        return PropertyLot(
            villa_id=row.villa_id,
            lot_id=row.lot_id,
            owner_name=row.owner_name,
            owner_phone=row.owner_phone,
            owner_email=row.owner_email,
            is_maintenance_up_to_date=row.is_maintenance_up_to_date,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        return self._to_domain(row) if row else None

    async def list_lots(
        self,
        villa_id: str,
        page: int = 0,
        limit: int = 50,
        is_maintenance_up_to_date: bool | None = None,
    ) -> tuple[list[PropertyLot], int]:
        norm_villa = normalize_villa_id(villa_id)
        base_stmt = select(PropertyLotORM).where(PropertyLotORM.villa_id == norm_villa)
        count_stmt = (
            select(func.count())
            .select_from(PropertyLotORM)
            .where(PropertyLotORM.villa_id == norm_villa)
        )

        if is_maintenance_up_to_date is not None:
            base_stmt = base_stmt.where(
                PropertyLotORM.is_maintenance_up_to_date == is_maintenance_up_to_date
            )
            count_stmt = count_stmt.where(
                PropertyLotORM.is_maintenance_up_to_date == is_maintenance_up_to_date
            )

        total = int((await self.session.execute(count_stmt)).scalar_one())
        items_stmt = (
            base_stmt.order_by(PropertyLotORM.lot_id.asc())
            .offset(page * limit)
            .limit(limit)
        )
        rows = (await self.session.execute(items_stmt)).scalars().all()
        return [self._to_domain(r) for r in rows], total

    async def update_lot_status(
        self,
        villa_id: str,
        lot_id: str,
        is_maintenance_up_to_date: bool,
        owner_name: str | None = None,
        owner_phone: str | None = None,
        owner_email: str | None = None,
    ) -> PropertyLot | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        if not row:
            return None

        row.is_maintenance_up_to_date = is_maintenance_up_to_date
        if owner_name is not None:
            row.owner_name = owner_name
        if owner_phone is not None:
            row.owner_phone = owner_phone
        if owner_email is not None:
            row.owner_email = owner_email
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return self._to_domain(row)

    async def upsert_bulk_lots(
        self, villa_id: str, items: list[BulkLotItem]
    ) -> tuple[int, int, list[PropertyLot], list[PropertyLot]]:
        norm_villa = normalize_villa_id(villa_id)
        now = datetime.now(UTC)
        created_count = 0
        updated_count = 0
        processed: list[PropertyLot] = []
        newly_created: list[PropertyLot] = []

        for item in items:
            norm_lot = normalize_lot_id(item.lot_id)
            row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
            if row:
                row.owner_name = item.owner_name
                if item.owner_phone is not None:
                    row.owner_phone = item.owner_phone
                if item.owner_email is not None:
                    row.owner_email = item.owner_email
                row.is_maintenance_up_to_date = item.is_maintenance_up_to_date
                row.updated_at = now
                domain_lot = self._to_domain(row)
                processed.append(domain_lot)
                updated_count += 1
            else:
                new_row = PropertyLotORM(
                    villa_id=norm_villa,
                    lot_id=norm_lot,
                    owner_name=item.owner_name,
                    owner_phone=item.owner_phone,
                    owner_email=item.owner_email,
                    is_maintenance_up_to_date=item.is_maintenance_up_to_date,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(new_row)
                domain_lot = self._to_domain(new_row)
                processed.append(domain_lot)
                newly_created.append(domain_lot)
                created_count += 1

        await self.session.flush()
        return created_count, updated_count, processed, newly_created


class PostgresAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _lot_to_domain(self, row: PropertyLotORM) -> PropertyLot:
        return PropertyLot(
            villa_id=row.villa_id,
            lot_id=row.lot_id,
            owner_name=row.owner_name,
            owner_phone=row.owner_phone,
            owner_email=row.owner_email,
            is_maintenance_up_to_date=row.is_maintenance_up_to_date,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _pass_to_domain(self, row: GuestPassORM) -> GuestPass:
        return GuestPass(
            villa_id=row.villa_id,
            code=row.code,
            lot_id=row.lot_id,
            pass_type=PassType(row.pass_type),
            visitor_info=row.visitor_info,
            status=PassStatus(row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            used_at=row.used_at,
            checked_in_dni=row.checked_in_dni,
            checked_in_plate=row.checked_in_plate,
        )

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        row = await self.session.get(
            PropertyLotORM, (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        )
        return self._lot_to_domain(row) if row else None

    async def save_pass(self, guest_pass: GuestPass) -> GuestPass:
        key = (normalize_villa_id(guest_pass.villa_id), guest_pass.code.upper())
        row = await self.session.get(GuestPassORM, key)
        if row:
            row.status = str(guest_pass.status)
            row.used_at = guest_pass.used_at
            row.expires_at = guest_pass.expires_at
            row.checked_in_dni = guest_pass.checked_in_dni
            row.checked_in_plate = guest_pass.checked_in_plate
        else:
            row = GuestPassORM(
                villa_id=key[0],
                code=key[1],
                lot_id=guest_pass.lot_id,
                pass_type=str(guest_pass.pass_type),
                visitor_info=guest_pass.visitor_info,
                status=str(guest_pass.status),
                created_at=guest_pass.created_at,
                expires_at=guest_pass.expires_at,
                used_at=guest_pass.used_at,
                checked_in_dni=guest_pass.checked_in_dni,
                checked_in_plate=guest_pass.checked_in_plate,
            )
            self.session.add(row)
        await self.session.flush()
        return self._pass_to_domain(row)

    async def get_pass_by_code(self, villa_id: str, code: str) -> GuestPass | None:
        row = await self.session.get(
            GuestPassORM, (normalize_villa_id(villa_id), code.upper())
        )
        return self._pass_to_domain(row) if row else None


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, row: UserORM) -> User:
        return User(
            villa_id=row.villa_id,
            username=row.username,
            password_hash=row.password_hash,
            role=UserRole(row.role),
            lot_id=row.lot_id,
            email=row.email,
            is_active=row.is_active,
            last_login_at=row.last_login_at,
            last_login_ip=row.last_login_ip,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _build_report(self, row: IPSecurityRecordORM) -> BlockedIPReport:
        unique_targets = list(dict.fromkeys(row.attempted_usernames or []))
        attack_type = (
            AttackType.PASSWORD_SPRAYING
            if len(unique_targets) > 1
            else AttackType.TARGETED_BRUTE_FORCE
        )
        return BlockedIPReport(
            villa_id=row.villa_id,
            ip_address=row.ip_address,
            failed_attempts=row.failed_attempts,
            is_blocked=row.is_blocked,
            attack_type=attack_type,
            attempted_usernames=unique_targets,
            suspected_lots_from_previous_logins=list(row.known_successful_users or []),
            blocked_at=row.blocked_at,
            last_user_agent=row.last_user_agent,
        )

    async def get_user(self, villa_id: str, username: str) -> User | None:
        row = await self.session.get(
            UserORM, (normalize_villa_id(villa_id), normalize_lot_id(username))
        )
        return self._to_domain(row) if row else None

    async def create_or_get_resident_user(
        self,
        villa_id: str,
        lot_id: str,
        initial_password: str,
        email: str | None = None,
    ) -> tuple[User, bool]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        row = await self.session.get(UserORM, (norm_villa, norm_lot))
        if row:
            if email:
                row.email = email
                await self.session.flush()
            return self._to_domain(row), False

        now = datetime.now(UTC)
        new_row = UserORM(
            villa_id=norm_villa,
            username=norm_lot,
            password_hash=hash_password(initial_password),
            role=str(UserRole.RESIDENT),
            lot_id=norm_lot,
            email=email,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.session.add(new_row)
        await self.session.flush()
        return self._to_domain(new_row), True

    async def update_password(
        self, villa_id: str, username: str, new_password_hash: str
    ) -> User | None:
        row = await self.session.get(
            UserORM, (normalize_villa_id(villa_id), normalize_lot_id(username))
        )
        if not row:
            return None
        row.password_hash = new_password_hash
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return self._to_domain(row)

    async def record_login_success(
        self, villa_id: str, username: str, client_ip: str
    ) -> User | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_username = normalize_lot_id(username)
        row = await self.session.get(UserORM, (norm_villa, norm_username))
        if not row:
            return None

        now = datetime.now(UTC)
        row.last_login_at = now
        row.last_login_ip = client_ip
        row.updated_at = now

        ip_row = await self.session.get(IPSecurityRecordORM, (norm_villa, client_ip))
        if not ip_row:
            ip_row = IPSecurityRecordORM(
                villa_id=norm_villa,
                ip_address=client_ip,
                failed_attempts=0,
                is_blocked=False,
                attempted_usernames=[],
                known_successful_users=[norm_username],
            )
            self.session.add(ip_row)
        else:
            known = list(ip_row.known_successful_users or [])
            if norm_username not in known:
                known.append(norm_username)
                ip_row.known_successful_users = known
            if not ip_row.is_blocked:
                ip_row.failed_attempts = 0
                ip_row.attempted_usernames = []

        await self.session.flush()
        return self._to_domain(row)

    async def record_login_failure(
        self,
        villa_id: str,
        attempted_username: str,
        client_ip: str,
        user_agent: str,
    ) -> BlockedIPReport:
        norm_villa = normalize_villa_id(villa_id)
        norm_target = normalize_lot_id(attempted_username)
        ip_row = await self.session.get(IPSecurityRecordORM, (norm_villa, client_ip))
        if not ip_row:
            ip_row = IPSecurityRecordORM(
                villa_id=norm_villa,
                ip_address=client_ip,
                failed_attempts=0,
                is_blocked=False,
                attempted_usernames=[],
                known_successful_users=[],
            )
            self.session.add(ip_row)

        ip_row.failed_attempts += 1
        attempts_list = list(ip_row.attempted_usernames or [])
        attempts_list.append(norm_target)
        ip_row.attempted_usernames = attempts_list
        ip_row.last_user_agent = user_agent

        if (
            ip_row.failed_attempts >= settings.max_failed_attempts_per_ip
            and not ip_row.is_blocked
        ):
            ip_row.is_blocked = True
            ip_row.blocked_at = datetime.now(UTC)

        await self.session.flush()
        return self._build_report(ip_row)

    async def is_ip_blocked(
        self, villa_id: str, client_ip: str
    ) -> BlockedIPReport | None:
        ip_row = await self.session.get(
            IPSecurityRecordORM, (normalize_villa_id(villa_id), client_ip)
        )
        if ip_row and ip_row.is_blocked:
            return self._build_report(ip_row)
        return None

    async def list_blocked_ips(self, villa_id: str) -> list[BlockedIPReport]:
        norm_villa = normalize_villa_id(villa_id)
        stmt = select(IPSecurityRecordORM).where(
            IPSecurityRecordORM.villa_id == norm_villa,
            IPSecurityRecordORM.is_blocked.is_(True),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._build_report(r) for r in rows]

    async def unblock_ip(self, villa_id: str, client_ip: str) -> bool:
        ip_row = await self.session.get(
            IPSecurityRecordORM, (normalize_villa_id(villa_id), client_ip)
        )
        if not ip_row or not ip_row.is_blocked:
            return False
        ip_row.is_blocked = False
        ip_row.failed_attempts = 0
        ip_row.attempted_usernames = []
        ip_row.blocked_at = None
        await self.session.flush()
        return True
