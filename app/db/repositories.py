from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    IPSecurityRecordORM,
    LotMembershipORM,
    PropertyLotORM,
    UserORM,
)
from app.models.lot import (
    BulkLotItem,
    LotMembership,
    LotType,
    PropertyLot,
    normalize_lot_id,
    normalize_phone_username,
    normalize_villa_id,
)
from app.models.user import (
    AttackType,
    BlockedIPReport,
    MembershipRole,
    User,
    UserRole,
)


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
            lot_type=LotType(row.lot_type or "LOT"),
            display_name=row.display_name,
            is_public=bool(row.is_public),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _mem_to_domain(
        self,
        row: LotMembershipORM,
        is_maintenance_up_to_date: bool | None = None,
    ) -> LotMembership:
        return LotMembership(
            villa_id=row.villa_id,
            lot_id=row.lot_id,
            phone=row.phone,
            full_name=row.full_name,
            document_id=row.document_id,
            membership_role=MembershipRole(row.membership_role),
            added_by=row.added_by,
            is_active=row.is_active,
            is_maintenance_up_to_date=is_maintenance_up_to_date,
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

        norm_phone = normalize_phone_username(row.owner_phone)
        if norm_phone:
            await self.upsert_membership(
                villa_id=norm_villa,
                lot_id=norm_lot,
                phone=norm_phone,
                full_name=row.owner_name,
                membership_role=MembershipRole.ANCHOR,
                added_by="ADMIN",
            )
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
                await self.session.flush()
                domain_lot = self._to_domain(new_row)
                processed.append(domain_lot)
                newly_created.append(domain_lot)
                created_count += 1

            norm_phone = normalize_phone_username(domain_lot.owner_phone)
            if norm_phone:
                await self.upsert_membership(
                    villa_id=norm_villa,
                    lot_id=norm_lot,
                    phone=norm_phone,
                    full_name=domain_lot.owner_name,
                    membership_role=MembershipRole.ANCHOR,
                    added_by="ADMIN",
                )

        await self.session.flush()
        return created_count, updated_count, processed, newly_created

    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        row = await self.session.get(
            LotMembershipORM, (norm_villa, norm_lot, norm_phone)
        )
        if not row or not row.is_active:
            return None
        lot_row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        return self._mem_to_domain(
            row, lot_row.is_maintenance_up_to_date if lot_row else None
        )

    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        stmt = (
            select(LotMembershipORM, PropertyLotORM.is_maintenance_up_to_date)
            .join(
                PropertyLotORM,
                (LotMembershipORM.villa_id == PropertyLotORM.villa_id)
                & (LotMembershipORM.lot_id == PropertyLotORM.lot_id),
            )
            .where(
                LotMembershipORM.villa_id == norm_villa,
                LotMembershipORM.phone == norm_phone,
                LotMembershipORM.is_active.is_(True),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        results = [self._mem_to_domain(r[0], r[1]) for r in rows]
        results.sort(
            key=lambda m: (
                0 if m.membership_role == MembershipRole.ANCHOR else 1,
                m.lot_id,
            )
        )
        return results

    async def list_lot_members(
        self, villa_id: str, lot_id: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        lot_row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        maint = lot_row.is_maintenance_up_to_date if lot_row else None
        stmt = select(LotMembershipORM).where(
            LotMembershipORM.villa_id == norm_villa,
            LotMembershipORM.lot_id == norm_lot,
            LotMembershipORM.is_active.is_(True),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        results = [self._mem_to_domain(r, maint) for r in rows]
        results.sort(
            key=lambda m: (
                0 if m.membership_role == MembershipRole.ANCHOR else 1,
                m.full_name,
            )
        )
        return results

    async def upsert_membership(
        self,
        villa_id: str,
        lot_id: str,
        phone: str,
        full_name: str,
        membership_role: MembershipRole,
        added_by: str = "ADMIN",
        document_id: str | None = None,
        is_active: bool = True,
    ) -> LotMembership:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        now = datetime.now(UTC)
        key = (norm_villa, norm_lot, norm_phone)
        row = await self.session.get(LotMembershipORM, key)
        if row:
            row.full_name = full_name
            row.membership_role = str(membership_role)
            row.added_by = added_by
            if document_id is not None:
                row.document_id = document_id
            row.is_active = is_active
            row.updated_at = now
        else:
            row = LotMembershipORM(
                villa_id=norm_villa,
                lot_id=norm_lot,
                phone=norm_phone,
                full_name=full_name,
                document_id=document_id,
                membership_role=str(membership_role),
                added_by=added_by,
                is_active=is_active,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        await self.session.flush()
        lot_row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        return self._mem_to_domain(
            row, lot_row.is_maintenance_up_to_date if lot_row else None
        )

    async def deactivate_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        row = await self.session.get(
            LotMembershipORM, (norm_villa, norm_lot, norm_phone)
        )
        if not row or not row.is_active:
            return None
        row.is_active = False
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        lot_row = await self.session.get(PropertyLotORM, (norm_villa, norm_lot))
        return self._mem_to_domain(
            row, lot_row.is_maintenance_up_to_date if lot_row else None
        )


class PostgresMembershipLookup:
    """Lecturas de lotes y membresías activas compartidas por visitas y carnet."""

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
            lot_type=LotType(row.lot_type or "LOT"),
            display_name=row.display_name,
            is_public=bool(row.is_public),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _mem_to_domain(self, row: LotMembershipORM) -> LotMembership:
        return LotMembership(
            villa_id=row.villa_id,
            lot_id=row.lot_id,
            phone=row.phone,
            full_name=row.full_name,
            document_id=row.document_id,
            membership_role=MembershipRole(row.membership_role),
            added_by=row.added_by,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_lot(self, villa_id: str, lot_id: str) -> PropertyLot | None:
        row = await self.session.get(
            PropertyLotORM, (normalize_villa_id(villa_id), normalize_lot_id(lot_id))
        )
        return self._lot_to_domain(row) if row else None

    async def get_membership(
        self, villa_id: str, lot_id: str, phone: str
    ) -> LotMembership | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_lot = normalize_lot_id(lot_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        row = await self.session.get(
            LotMembershipORM, (norm_villa, norm_lot, norm_phone)
        )
        if not row or not row.is_active:
            return None
        return self._mem_to_domain(row)

    async def list_memberships_by_phone(
        self, villa_id: str, phone: str
    ) -> list[LotMembership]:
        norm_villa = normalize_villa_id(villa_id)
        norm_phone = normalize_phone_username(phone) or phone.strip()
        stmt = select(LotMembershipORM).where(
            LotMembershipORM.villa_id == norm_villa,
            LotMembershipORM.phone == norm_phone,
            LotMembershipORM.is_active.is_(True),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._mem_to_domain(r) for r in rows]


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
            full_name=row.full_name,
            document_id=row.document_id,
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
        norm_villa = normalize_villa_id(villa_id)
        norm_user = normalize_lot_id(username)
        row = await self.session.get(UserORM, (norm_villa, norm_user))
        if row:
            return self._to_domain(row)
        if norm_user.isdigit() and len(norm_user) == 9:
            return await self.get_user_any_villa(norm_user)
        return None

    async def get_user_any_villa(self, username: str) -> User | None:
        norm_user = normalize_lot_id(username)
        stmt = select(UserORM).where(
            UserORM.username == norm_user, UserORM.is_active.is_(True)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return None
        domains = [self._to_domain(r) for r in rows]
        domains.sort(
            key=lambda u: (
                0 if u.role == UserRole.SUPER_ADMIN else 1,
                0 if u.last_login_at is not None else 1,
            )
        )
        return domains[0]

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
        row = await self.session.get(UserORM, (norm_villa, actual_username))
        if row:
            if email:
                row.email = email
            if full_name:
                row.full_name = full_name
            if document_id:
                row.document_id = document_id
            await self.session.flush()
            return self._to_domain(row), False

        now = datetime.now(UTC)
        new_row = UserORM(
            villa_id=norm_villa,
            username=actual_username,
            password_hash=hash_password(initial_password),
            role=str(role),
            lot_id=norm_lot,
            full_name=full_name,
            document_id=document_id,
            email=email,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.session.add(new_row)
        await self.session.flush()
        return self._to_domain(new_row), True

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
        norm_villa = normalize_villa_id(villa_id)
        norm_user = normalize_lot_id(username)
        norm_lot = normalize_lot_id(lot_id) if lot_id else None
        now = datetime.now(UTC)
        row = await self.session.get(UserORM, (norm_villa, norm_user))
        if row:
            row.password_hash = password_hash
            if row.role not in (str(UserRole.SUPER_ADMIN), str(UserRole.ADMIN)):
                row.role = str(role)
            if norm_lot is not None:
                row.lot_id = norm_lot
            if full_name is not None:
                row.full_name = full_name
            if document_id is not None:
                row.document_id = document_id
            if email is not None:
                row.email = email
            if clear_last_login:
                row.last_login_at = None
            row.updated_at = now
        else:
            row = UserORM(
                villa_id=norm_villa,
                username=norm_user,
                password_hash=password_hash,
                role=str(role),
                lot_id=norm_lot,
                full_name=full_name,
                document_id=document_id,
                email=email,
                is_active=True,
                last_login_at=None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)

        if norm_user.isdigit() and len(norm_user) == 9:
            stmt = select(UserORM).where(UserORM.username == norm_user)
            other_rows = (await self.session.execute(stmt)).scalars().all()
            for o in other_rows:
                o.password_hash = password_hash

        await self.session.flush()
        return self._to_domain(row)

    async def update_password(
        self,
        villa_id: str,
        username: str,
        new_password_hash: str,
        *,
        clear_last_login: bool = False,
    ) -> User | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_user = normalize_lot_id(username)
        row = await self.session.get(UserORM, (norm_villa, norm_user))
        if not row and norm_user.isdigit() and len(norm_user) == 9:
            stmt = select(UserORM).where(UserORM.username == norm_user)
            rows = (await self.session.execute(stmt)).scalars().all()
            if rows:
                row = rows[0]
        if not row:
            return None

        now = datetime.now(UTC)
        row.password_hash = new_password_hash
        if clear_last_login:
            row.last_login_at = None
        row.updated_at = now

        if norm_user.isdigit() and len(norm_user) == 9:
            stmt = select(UserORM).where(UserORM.username == norm_user)
            all_rows = (await self.session.execute(stmt)).scalars().all()
            for r in all_rows:
                r.password_hash = new_password_hash
                if clear_last_login:
                    r.last_login_at = None
                r.updated_at = now

        await self.session.flush()
        return self._to_domain(row)

    async def record_login_success(
        self, villa_id: str, username: str, client_ip: str
    ) -> User | None:
        norm_villa = normalize_villa_id(villa_id)
        norm_username = normalize_lot_id(username)
        row = await self.session.get(UserORM, (norm_villa, norm_username))
        if not row and norm_username.isdigit() and len(norm_username) == 9:
            stmt = select(UserORM).where(UserORM.username == norm_username)
            rows = (await self.session.execute(stmt)).scalars().all()
            if rows:
                row = rows[0]
        if not row:
            return None

        now = datetime.now(UTC)
        row.last_login_at = now
        row.last_login_ip = client_ip
        row.updated_at = now

        if norm_username.isdigit() and len(norm_username) == 9:
            stmt = select(UserORM).where(UserORM.username == norm_username)
            for r in (await self.session.execute(stmt)).scalars().all():
                r.last_login_at = now
                r.last_login_ip = client_ip

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
