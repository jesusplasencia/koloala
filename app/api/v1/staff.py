"""Personal de la Villa (vigilantes y administradores). Todos ingresan con su celular + DNI + PIN."""

from fastapi import APIRouter, HTTPException, status

from app.api.v1.auth import PeopleRepoDep
from app.core.audit import log_audit_event
from app.core.pii import mask_doc, normalize_document_id
from app.core.security import AdminDep
from app.models.lot import normalize_phone_username
from app.models.user import CreateStaffRequest, Person, StaffMemberView, StaffRole, StaffRoleName, UserRole

router = APIRouter(prefix="/admin/staff", tags=["Villa Staff"])


def _view(role: StaffRole, person: Person) -> StaffMemberView:
    return StaffMemberView(
        villa_id=role.villa_id,
        phone=person.phone,
        full_name=person.full_name,
        role=role.role,
        document_id=mask_doc(None, person.document_id[-2:]) if person.document_id else None,
        is_active=role.is_active,
        has_activated=person.last_login_at is not None,
    )


@router.get("", response_model=list[StaffMemberView])
async def list_staff(admin: AdminDep, people: PeopleRepoDep) -> list[StaffMemberView]:
    """[SOLO ADMIN] Vigilantes y administradores de la Villa."""
    return [_view(r, p) for r, p in await people.list_staff(admin.villa_id)]


@router.post("", response_model=StaffMemberView, status_code=status.HTTP_201_CREATED)
async def add_staff(payload: CreateStaffRequest, admin: AdminDep, people: PeopleRepoDep) -> StaffMemberView:
    """[SOLO ADMIN] Da de alta a un vigilante. Solo el SUPER_ADMIN puede nombrar administradores."""
    if payload.role == StaffRoleName.ADMIN and admin.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el Super Admin puede nombrar administradores.")
    phone = normalize_phone_username(payload.phone) or ""
    if not (phone.isdigit() and len(phone) == 9):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El celular debe tener 9 dígitos.")
    try:
        doc = normalize_document_id(payload.document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    person = await people.upsert_person(phone, payload.full_name.strip(), doc, overwrite=True)
    role = await people.upsert_staff_role(admin.villa_id, person.id, payload.role, is_active=True)
    log_audit_event(
        event="STAFF_ADDED",
        actor=f"{admin.villa_id}:{admin.username}",
        client_ip="internal",
        user_agent="api",
        details={"villa_id": admin.villa_id, "phone": phone, "role": str(payload.role)},
    )
    return _view(role, person)


@router.delete("/{phone}", response_model=StaffMemberView)
async def deactivate_staff(phone: str, admin: AdminDep, people: PeopleRepoDep) -> StaffMemberView:
    """[SOLO ADMIN] Baja lógica del personal (is_active=False). Sus sesiones dejan de valer al cerrar acceso."""
    person = await people.get_by_phone(phone)
    current = await people.get_staff_role(admin.villa_id, person.id) if person else None
    if not person or not current:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esa persona no es personal activo de la Villa.")
    if current.role == StaffRoleName.ADMIN and admin.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el Super Admin puede retirar administradores.")
    role = await people.upsert_staff_role(admin.villa_id, person.id, current.role, is_active=False)
    await people.reset_access(person.id)
    log_audit_event(
        event="STAFF_DEACTIVATED",
        actor=f"{admin.villa_id}:{admin.username}",
        client_ip="internal",
        user_agent="api",
        details={"villa_id": admin.villa_id, "phone": person.phone},
    )
    return _view(role, person)
