"""Script de inicialización (Seed) para Koloala en PostgreSQL (Supabase / RDS).

Crea o actualiza de forma idempotente en Villa Club 3 (VC3):
1. Usuario ADMIN (Jesús) -> username: ADMIN
2. Usuario GUARD (Papá)  -> username: GUARDIA1
3. Lote P20 (VC3) + su cuenta RESIDENT con PIN numérico de 8 dígitos
"""

import asyncio
from datetime import UTC, datetime

from app.core.security import generate_initial_lot_password, hash_password
from app.db.models import PropertyLotORM, UserORM
from app.db.session import AsyncSessionLocal
from app.models.user import UserRole


async def seed_vc3_initial_data() -> None:
    villa_id = "VC3"
    now = datetime.now(UTC)

    # Credenciales iniciales (se pueden cambiar luego desde /api/v1/auth/change-password)
    admin_username = "ADMIN"
    admin_password = "AdminPassword2026!"

    guard_username = "GUARDIA1"
    guard_password = "GuardiaPassword2026!"

    lot_id = "P20"
    lot_owner_name = "Familia Plasencia (P20)"
    resident_pin = generate_initial_lot_password()

    async with AsyncSessionLocal() as session:
        # 1. Crear o actualizar cuenta ADMIN (Jesús)
        admin_user = await session.get(UserORM, (villa_id, admin_username))
        if not admin_user:
            admin_user = UserORM(
                villa_id=villa_id,
                username=admin_username,
                password_hash=hash_password(admin_password),
                role=str(UserRole.ADMIN),
                lot_id=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(admin_user)
            admin_status = "CREADO"
        else:
            admin_status = "YA EXISTÍA (mantiene su clave actual)"

        # 2. Crear o actualizar cuenta GUARD (Papá)
        guard_user = await session.get(UserORM, (villa_id, guard_username))
        if not guard_user:
            guard_user = UserORM(
                villa_id=villa_id,
                username=guard_username,
                password_hash=hash_password(guard_password),
                role=str(UserRole.GUARD),
                lot_id=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(guard_user)
            guard_status = "CREADO"
        else:
            guard_status = "YA EXISTÍA (mantiene su clave actual)"

        # 3. Crear o actualizar el Lote P20 en VC3
        lot_row = await session.get(PropertyLotORM, (villa_id, lot_id))
        if not lot_row:
            lot_row = PropertyLotORM(
                villa_id=villa_id,
                lot_id=lot_id,
                owner_name=lot_owner_name,
                is_maintenance_up_to_date=True,
                created_at=now,
                updated_at=now,
            )
            session.add(lot_row)
            lot_status = "CREADO (Al día = True)"
        else:
            lot_row.is_maintenance_up_to_date = True
            lot_row.updated_at = now
            lot_status = "ACTUALIZADO (Al día = True)"

        # 4. Crear o verificar usuario RESIDENT para el lote P20
        resident_user = await session.get(UserORM, (villa_id, lot_id))
        if not resident_user:
            resident_user = UserORM(
                villa_id=villa_id,
                username=lot_id,
                password_hash=hash_password(resident_pin),
                role=str(UserRole.RESIDENT),
                lot_id=lot_id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(resident_user)
            resident_pin_display = resident_pin
        else:
            # Si ya existía, regeneramos un PIN de 8 dígitos nuevo para que lo tengas a mano
            resident_user.password_hash = hash_password(resident_pin)
            resident_user.updated_at = now
            resident_pin_display = f"{resident_pin} (PIN actualizado)"

        await session.commit()

    print("\n" + "=" * 72)
    print("[OK] SEED COMPLETADO EN SUPABASE (Usando rol restringido koloala_runtime)")
    print("=" * 72)
    print(f"Villa: {villa_id} (Villa Club 3)")
    print("-" * 72)
    print(
        f"1. ADMIN (Tu)        -> Usuario: {admin_username:<10} "
        f"| Clave: {admin_password} ({admin_status})"
    )
    print(
        f"2. GUARDIA (Tu Papa) -> Usuario: {guard_username:<10} "
        f"| Clave: {guard_password} ({guard_status})"
    )
    print(
        f"3. LOTE RESIDENTE    -> Usuario: {lot_id:<10} "
        f"| Clave (PIN 8 digitos): {resident_pin_display} ({lot_status})"
    )
    print("=" * 72 + "\n")


if __name__ == "__main__":
    asyncio.run(seed_vc3_initial_data())
