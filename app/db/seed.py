"""Seed idempotente de desarrollo para Villa Club 3 (VC3) con el modelo people + staff_roles + memberships.

Cuentas para probar los 3 roles:
1. 916220518  Jesús Plasencia          -> SUPER_ADMIN (oficial) y ANCHOR de VC3-P20
2. 934253226  Administrador VC3        -> ADMIN de VC3 (oficial)
3. 900000001  Vigilante de Prueba      -> GUARD de VC3 (número FICTICIO, solo para pruebas)
Además: el lote P20 y el catálogo ML/CA. Los familiares se agregan desde la app (siempre con DNI).

Los PIN se generan al crear la persona y se imprimen SOLO en esta consola.
Re-ejecutar el seed no cambia contraseñas ni datos ya existentes.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.pii import normalize_document_id
from app.core.security import generate_initial_lot_password, hash_password
from app.db.models import LotMembershipORM, PersonORM, PropertyLotORM, StaffRoleORM
from app.db.session import AsyncSessionLocal

VILLA = "VC3"


async def _person(session, phone: str, name: str, *, super_admin: bool = False, with_pin: bool = True) -> tuple[PersonORM, str]:
    row = (await session.execute(select(PersonORM).where(PersonORM.phone == phone))).scalars().first()
    if row:
        return row, "(ya existía, sin cambios)"
    now = datetime.now(UTC)
    pin = generate_initial_lot_password() if with_pin else None
    row = PersonORM(
        id=uuid.uuid4(),
        phone=phone,
        full_name=name,
        password_hash=hash_password(pin) if pin else None,
        is_super_admin=super_admin,
        last_login_at=now if pin else None,  # cuentas sembradas listas para entrar con su PIN
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row, (f"PIN {pin}" if pin else "sin PIN: activa con su DNI cuando el admin lo registre")


def _fill_dni(person: PersonORM, dni: str | None) -> str:
    """Completa el DNI solo si está vacío (nunca reemplaza uno existente ni toca el PIN)."""
    if person.document_id:
        return "DNI ya registrado"
    if not dni:
        return "SIN DNI (define SEED_SUPER_ADMIN_DNI en tu .env y vuelve a correr el seed)"
    person.document_id = normalize_document_id(dni)
    person.updated_at = datetime.now(UTC)
    return "DNI registrado"


async def _staff(session, person: PersonORM, role: str) -> None:
    row = await session.get(StaffRoleORM, (VILLA, person.id))
    if not row:
        session.add(StaffRoleORM(villa_id=VILLA, person_id=person.id, role=role, is_active=True))


async def _lot(session, lot_id: str, *, lot_type: str = "LOT", name: str | None = None, public: bool = False) -> None:
    row = await session.get(PropertyLotORM, (VILLA, lot_id))
    if not row:
        row = PropertyLotORM(villa_id=VILLA, lot_id=lot_id, is_maintenance_up_to_date=True)
        session.add(row)
    row.lot_type = lot_type
    row.display_name = name
    row.is_public = public
    row.updated_at = datetime.now(UTC)
    await session.flush()


async def _membership(session, lot_id: str, person: PersonORM, role: str, added_by: PersonORM | None = None) -> None:
    row = await session.get(LotMembershipORM, (VILLA, lot_id, person.id))
    if not row:
        session.add(
            LotMembershipORM(
                villa_id=VILLA,
                lot_id=lot_id,
                person_id=person.id,
                membership_role=role,
                added_by_person_id=added_by.id if added_by else None,
                is_active=True,
            )
        )


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        jesus, jesus_pin = await _person(session, "916220518", "Jesus Plasencia", super_admin=True)
        admin, admin_pin = await _person(session, "934253226", "Administrador Villa Club 3")
        guard, guard_pin = await _person(session, "900000001", "Vigilante de Prueba")
        jesus_dni = _fill_dni(jesus, settings.seed_super_admin_dni)
        admin_dni = _fill_dni(admin, "40000001")  # ficticio
        guard_dni = _fill_dni(guard, "40000003")  # ficticio

        await _staff(session, admin, "ADMIN")
        await _staff(session, guard, "GUARD")

        await _lot(session, "P20")
        await _membership(session, "P20", jesus, "ANCHOR")

        for code, name, lot_type in (
            ("ML01", "Panadería", "MINI_LOT"),
            ("ML02", "Botica", "MINI_LOT"),
            ("CA01", "Canchita de Fútbol 1", "COMMON_AREA"),
            ("CA02", "Parque 1", "COMMON_AREA"),
        ):
            await _lot(session, code, lot_type=lot_type, name=name, public=True)

        await session.commit()

    print("\n" + "=" * 72)
    print(f"[OK] SEED {VILLA} (people + staff_roles + lot_memberships)")
    print("=" * 72)
    print(f"SUPER_ADMIN + ANCHOR P20 -> 916220518 Jesús Plasencia   | {jesus_pin} | {jesus_dni}")
    print(f"ADMIN VC3               -> 934253226 Administrador VC3  | {admin_pin} | {admin_dni}")
    print(f"GUARD VC3 (ficticio)    -> 900000001 Vigilante de Prueba | {guard_pin} | {guard_dni}")
    print("Catálogo: ML01 Panadería, ML02 Botica, CA01 Canchita de Fútbol 1, CA02 Parque 1")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    asyncio.run(seed())
