"""Script de inicialización (Seed) 3NF para Koloala en PostgreSQL (Supabase / RDS).

Crea o actualiza de forma idempotente en Villa Club 3 (VC3):
1. SUPER_ADMIN de la App (Jesús Plasencia) -> username/celular: 916220518 (y ANCHOR de VC3-P20)
2. ADMIN de Villa Club 3                   -> username/celular: 934253226
3. Usuario GUARD (Garita)                  -> username: GUARDIA1
4. Lote P20 (VC3) + Membresías 3NF (ANCHOR: 916220518, HOUSEHOLD: 980180219 Angel Plasencia)
"""

import asyncio
from datetime import UTC, datetime

from app.core.security import generate_initial_lot_password, hash_password
from app.db.models import LotMembershipORM, PropertyLotORM, UserORM
from app.db.session import AsyncSessionLocal
from app.models.user import MembershipRole, UserRole


async def seed_vc3_initial_data() -> None:
    villa_id = "VC3"
    now = datetime.now(UTC)

    super_admin_phone = "916220518"
    super_admin_pin = "91622051"

    vc3_admin_phone = "934253226"
    vc3_admin_pin = "93425322"

    legacy_admin_username = "ADMIN"
    legacy_admin_password = "AdminPassword2026!"

    guard_username = "GUARDIA1"
    guard_password = "GuardiaPassword2026!"

    lot_id = "P20"
    lot_owner_name = "Jesus Plasencia"
    resident_pin = generate_initial_lot_password()

    async with AsyncSessionLocal() as session:
        # 1. Crear o actualizar SUPER_ADMIN de la App (Jesús Plasencia: 916220518)
        super_admin = await session.get(UserORM, (villa_id, super_admin_phone))
        if not super_admin:
            super_admin = UserORM(
                villa_id=villa_id,
                username=super_admin_phone,
                password_hash=hash_password(super_admin_pin),
                role=str(UserRole.SUPER_ADMIN),
                lot_id=lot_id,
                full_name="Jesus Plasencia",
                is_active=True,
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(super_admin)
            super_status = "CREADO"
        else:
            super_admin.role = str(UserRole.SUPER_ADMIN)
            super_admin.lot_id = lot_id
            super_admin.full_name = "Jesus Plasencia"
            super_status = "ACTUALIZADO (SUPER_ADMIN + P20)"

        # 2. Crear o actualizar ADMIN oficial de Villa Club 3 (934253226)
        vc3_admin = await session.get(UserORM, (villa_id, vc3_admin_phone))
        if not vc3_admin:
            vc3_admin = UserORM(
                villa_id=villa_id,
                username=vc3_admin_phone,
                password_hash=hash_password(vc3_admin_pin),
                role=str(UserRole.ADMIN),
                lot_id=None,
                full_name="Administrador Villa Club 3",
                is_active=True,
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(vc3_admin)
            vc3_admin_status = "CREADO"
        else:
            vc3_admin.role = str(UserRole.ADMIN)
            vc3_admin_status = "YA EXISTIA"

        # 3. Mantener cuenta ADMIN legacy por compatibilidad
        admin_user = await session.get(UserORM, (villa_id, legacy_admin_username))
        if not admin_user:
            admin_user = UserORM(
                villa_id=villa_id,
                username=legacy_admin_username,
                password_hash=hash_password(legacy_admin_password),
                role=str(UserRole.ADMIN),
                lot_id=None,
                is_active=True,
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(admin_user)

        # 4. Crear o actualizar cuenta GUARD (Garita)
        guard_user = await session.get(UserORM, (villa_id, guard_username))
        if not guard_user:
            guard_user = UserORM(
                villa_id=villa_id,
                username=guard_username,
                password_hash=hash_password(guard_password),
                role=str(UserRole.GUARD),
                lot_id=None,
                full_name="Vigilante Garita VC3",
                is_active=True,
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(guard_user)
            guard_status = "CREADO"
        else:
            guard_status = "YA EXISTIA"

        # 5. Crear o actualizar el Lote P20 en VC3
        lot_row = await session.get(PropertyLotORM, (villa_id, lot_id))
        if not lot_row:
            lot_row = PropertyLotORM(
                villa_id=villa_id,
                lot_id=lot_id,
                owner_name=lot_owner_name,
                owner_phone=super_admin_phone,
                is_maintenance_up_to_date=True,
                created_at=now,
                updated_at=now,
            )
            session.add(lot_row)
            lot_status = "CREADO (Al dia = True)"
        else:
            lot_row.owner_name = lot_owner_name
            lot_row.owner_phone = super_admin_phone
            lot_row.is_maintenance_up_to_date = True
            lot_row.updated_at = now
            lot_status = "ACTUALIZADO (Al dia = True)"
        await session.flush()

        # 6. Crear o actualizar Membresías 3NF en lot_memberships para P20
        anchor_mem = await session.get(
            LotMembershipORM, (villa_id, lot_id, super_admin_phone)
        )
        if not anchor_mem:
            session.add(
                LotMembershipORM(
                    villa_id=villa_id,
                    lot_id=lot_id,
                    phone=super_admin_phone,
                    full_name="Jesus Plasencia",
                    membership_role=str(MembershipRole.ANCHOR),
                    added_by=super_admin_phone,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )

        household_phone = "980180219"
        household_mem = await session.get(
            LotMembershipORM, (villa_id, lot_id, household_phone)
        )
        if not household_mem:
            session.add(
                LotMembershipORM(
                    villa_id=villa_id,
                    lot_id=lot_id,
                    phone=household_phone,
                    full_name="Angel Plasencia",
                    membership_role=str(MembershipRole.HOUSEHOLD),
                    added_by=super_admin_phone,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )

        # 7. Mantener cuenta RESIDENT P20 por compatibilidad
        resident_user = await session.get(UserORM, (villa_id, lot_id))
        if not resident_user:
            resident_user = UserORM(
                villa_id=villa_id,
                username=lot_id,
                password_hash=hash_password(resident_pin),
                role=str(UserRole.RESIDENT),
                lot_id=lot_id,
                full_name=lot_owner_name,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(resident_user)

        await session.commit()

    print("\n" + "=" * 76)
    print("[OK] SEED 3NF COMPLETADO EN SUPABASE (Usando rol restringido koloala_runtime)")
    print("=" * 76)
    print(f"Villa: {villa_id} (Villa Club 3)")
    print("-" * 76)
    print(
        f"1. SUPER ADMIN APP (Jesus) -> Celular: {super_admin_phone:<10} "
        f"| PIN: {super_admin_pin} ({super_status})"
    )
    print(
        f"2. ADMIN VILLA 3           -> Celular: {vc3_admin_phone:<10} "
        f"| PIN: {vc3_admin_pin} ({vc3_admin_status})"
    )
    print(
        f"3. GUARDIA GARITA          -> Usuario: {guard_username:<10} "
        f"| Clave: {guard_password} ({guard_status})"
    )
    print(
        f"4. LOTE P20 (3NF)          -> ANCHOR: {super_admin_phone} | HOUSEHOLD: 980180219 ({lot_status})"
    )
    print("=" * 76 + "\n")


if __name__ == "__main__":
    asyncio.run(seed_vc3_initial_data())
