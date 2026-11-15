import hmac
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.audit import get_client_ip, get_user_agent, log_audit_event
from app.core.config import settings
from app.core.pii import normalize_doc, normalize_document_id
from app.core.security import (
    AdminDep,
    CurrentIdentityDep,
    VillaIdDep,
    create_access_token,
    generate_initial_lot_password,
    get_people_repo,
    hash_password,
    verify_password,
)
from app.core.whatsapp import send_whatsapp_pin_notification
from app.db.repositories import PostgresLotRepository
from app.db.session import AsyncSessionLocal
from app.models.lot import normalize_peru_phone, normalize_phone_username
from app.models.user import (
    ActivateLotPinRequest,
    ActivateLotPinResponse,
    BlockedIPReport,
    ChangePasswordRequest,
    LoginRequest,
    MembershipRole,
    Person,
    StaffRoleName,
    TokenResponse,
    UserLookupRequest,
    UserLookupResponse,
    UserLookupStatus,
    UserRole,
)
from app.repositories.lots_repo import LotRepositoryProtocol, in_memory_lot_repo
from app.repositories.people_repo import PeopleRepositoryProtocol

router = APIRouter(prefix="/auth", tags=["Authentication & Security"])

# Alias para compatibilidad con overrides existentes en tests
get_user_repo = get_people_repo


async def get_lot_repo() -> AsyncIterator[LotRepositoryProtocol]:
    if not settings.use_postgres:
        yield in_memory_lot_repo
        return
    async with AsyncSessionLocal() as session:
        try:
            yield PostgresLotRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


PeopleRepoDep = Annotated[Any, Depends(get_people_repo)]
LotRepoDep = Annotated[LotRepositoryProtocol, Depends(get_lot_repo)]

NO_ACCOUNT_MESSAGE = (
    "Este número no tiene cuenta. Si visitas una casa, pide a tu anfitrión su QR; "
    "si vas a un negocio o área común, acércate a garita con tu DNI."
)


async def _villa_access(
    people: PeopleRepositoryProtocol, lots: LotRepositoryProtocol, villa_id: str, person: Person
) -> tuple[UserRole | None, list]:
    """Rol efectivo de la persona en esta Villa + sus membresías activas."""
    memberships = await lots.list_memberships_by_person(villa_id, person.id)
    if person.is_super_admin:
        return UserRole.SUPER_ADMIN, memberships
    staff = await people.get_staff_role(villa_id, person.id)
    if staff:
        return (UserRole.ADMIN if staff.role == StaffRoleName.ADMIN else UserRole.GUARD), memberships
    if memberships:
        return UserRole.RESIDENT, memberships
    return None, memberships


@router.post("/lookup", response_model=UserLookupResponse)
async def lookup_user_login_state(
    payload: UserLookupRequest,
    villa_id: VillaIdDep,
    people: PeopleRepoDep,
    lots: LotRepoDep,
) -> UserLookupResponse:
    """
    Paso 1 del login (todos ingresan con su celular de 9 dígitos):
    - READY_FOR_PASSWORD: ya activó su cuenta.
    - NEEDS_DOCUMENT: tiene acceso en esta Villa y aún no activa: se le pedirá su DNI/CE.
    - UNREGISTERED_NO_ACCOUNT: sin acceso (los visitantes no crean cuenta).
    Nunca devuelve nombre, rol ni lotes antes de autenticar.
    """
    phone = normalize_phone_username(payload.username) or ""
    if not (phone.isdigit() and len(phone) == 9):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ingresa tu celular de 9 dígitos.")
    person = await people.get_by_phone(phone)
    role = (await _villa_access(people, lots, villa_id, person))[0] if person and person.is_active else None
    if person and role and person.last_login_at is not None:
        status_value = UserLookupStatus.READY_FOR_PASSWORD
        message = None
    elif person and role:
        status_value = UserLookupStatus.NEEDS_DOCUMENT
        message = "Confirma tu DNI o Carnet de Extranjería para recibir tu PIN por WhatsApp."
    else:
        status_value = UserLookupStatus.UNREGISTERED_NO_ACCOUNT
        message = NO_ACCOUNT_MESSAGE
    return UserLookupResponse(villa_id=villa_id, username=phone, status=status_value, message=message)


@router.post("/activate-pin", response_model=ActivateLotPinResponse)
async def activate_lot_pin_via_whatsapp(
    payload: ActivateLotPinRequest,
    request: Request,
    villa_id: VillaIdDep,
    people: PeopleRepoDep,
    lots: LotRepoDep,
) -> ActivateLotPinResponse:
    """
    Primer ingreso: confirma el DNI/CE registrado (tiempo constante), máximo N envíos por persona
    cada 24 h, genera un PIN de 8 dígitos en RAM (solo se guarda su hash) y lo envía por WhatsApp.
    Los DNI incorrectos cuentan para el bloqueo de IP (3 fallos).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    phone = normalize_phone_username(payload.username) or ""
    actor_label = f"{villa_id}:{phone}"

    blocked = await people.is_ip_blocked(villa_id, client_ip)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa ({blocked.attack_type}).",
        )
    try:
        provided_doc = normalize_document_id(payload.document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    person = await people.get_by_phone(phone)
    if person and person.last_login_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta cuenta ya tiene un PIN activo. Ingresa con tu PIN.")
    role, memberships = await _villa_access(people, lots, villa_id, person) if person else (None, [])
    if not person or not role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Este número no está registrado en la Villa.")

    since = datetime.now(UTC) - timedelta(hours=24)
    if person.access_reset_at and person.access_reset_at > since:
        since = person.access_reset_at  # el Admin restableció el acceso: el conteo vuelve a cero
    if await people.count_pin_requests(villa_id, person.id, since) >= settings.max_pin_sends_per_phone_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ya enviamos tu PIN varias veces hoy. Revisa tu WhatsApp o inténtalo mañana.",
        )

    registered = normalize_doc(person.document_id)
    if not registered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aún no tienes un DNI registrado. Pide al Administrador o al titular de tu lote que lo registre.",
        )
    if not hmac.compare_digest(provided_doc or "", registered):
        await people.record_pin_request(villa_id, person.id, client_ip, ok=False)
        report = await people.record_login_failure(villa_id, phone, client_ip, user_agent)
        log_audit_event(
            event="AUTH_DOCUMENT_ACTIVATION_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": villa_id, "attempt": report.failed_attempts},
        )
        if report.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Los datos no coinciden con los registrados (intento {report.failed_attempts} de {settings.max_failed_attempts_per_ip}).",
        )

    lot_label = memberships[0].lot_id if memberships else None
    fresh_pin = generate_initial_lot_password()
    await people.set_password(person.id, hash_password(fresh_pin))
    sent_ok, channel = await send_whatsapp_pin_notification(
        phone=normalize_peru_phone(phone) or f"51{phone}",
        villa_id=villa_id,
        lot_id=lot_label or "PERSONAL",
        owner_name=person.full_name or phone,
        pin_password=fresh_pin,
    )
    if not sent_ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo despachar el mensaje de WhatsApp ({channel}).")
    await people.record_pin_request(villa_id, person.id, client_ip, ok=True)
    log_audit_event(
        event="AUTH_PIN_ACTIVATED_VIA_WHATSAPP",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
        details={"villa_id": villa_id, "role": str(role), "lot_id": lot_label, "channel": channel},
    )
    return ActivateLotPinResponse(
        villa_id=villa_id,
        lot_id=lot_label,
        delivery_channel=channel,
        message="Te enviamos tu PIN de 8 dígitos por WhatsApp.",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    villa_id: VillaIdDep,
    people: PeopleRepoDep,
    lots: LotRepoDep,
) -> TokenResponse:
    """Login con celular + PIN/contraseña. El rol se decide por Villa: super admin, personal o residente."""
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    phone = normalize_phone_username(payload.username) or payload.username.strip()
    actor_label = f"{villa_id}:{phone}"

    blocked = await people.is_ip_blocked(villa_id, client_ip)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa ({blocked.attack_type}).",
        )

    person = await people.get_by_phone(phone)
    if not person or not person.is_active or not person.password_hash or not verify_password(payload.password, person.password_hash):
        report = await people.record_login_failure(villa_id, phone, client_ip, user_agent)
        if report.is_blocked:
            log_audit_event(
                event="SECURITY_IP_BLOCKED_BRUTE_FORCE",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details=report.model_dump(mode="json"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos "
                    f"({report.attack_type}). Este incidente ha sido reportado a la Administración."
                ),
            )
        log_audit_event(
            event="AUTH_LOGIN_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": villa_id, "attempt": report.failed_attempts},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Credenciales inválidas en {villa_id} (Intento {report.failed_attempts} de {settings.max_failed_attempts_per_ip}).",
        )

    role, memberships = await _villa_access(people, lots, villa_id, person)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso (residente ni personal) en esta Villa.")

    primary = memberships[0] if memberships else None
    lot_id = primary.lot_id if primary else None
    mem_role: MembershipRole | None = primary.membership_role if primary else None
    await people.record_login_success(person.id, villa_id, client_ip)
    token = create_access_token(
        person_id=person.id,
        role=role,
        villa_id=villa_id,
        lot_id=lot_id,
        membership_role=mem_role,
        full_name=person.full_name,
        token_version=person.token_version,
    )
    log_audit_event(
        event="AUTH_LOGIN_SUCCESS",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
        details={"villa_id": villa_id, "role": str(role), "lot_id": lot_id},
    )
    return TokenResponse(
        access_token=token,
        expires_in_days=settings.access_token_expire_minutes // (60 * 24),
        villa_id=villa_id,
        username=person.phone,
        role=role,
        lot_id=lot_id,
        membership_role=mem_role,
        full_name=person.full_name,
        available_lots=[m.lot_id for m in memberships],
    )


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    identity: CurrentIdentityDep,
    people: PeopleRepoDep,
) -> dict[str, str]:
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    actor_label = f"{identity.villa_id}:{identity.username}"
    person = await people.get_by_id(identity.person_id)
    if not person or not person.password_hash or not verify_password(payload.current_password, person.password_hash):
        log_audit_event(event="AUTH_PASSWORD_CHANGE_FAILED", actor=actor_label, client_ip=client_ip, user_agent=user_agent)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual ingresada no es correcta.")
    await people.set_password(person.id, hash_password(payload.new_password))
    log_audit_event(event="AUTH_PASSWORD_CHANGED", actor=actor_label, client_ip=client_ip, user_agent=user_agent)
    return {"message": "Contraseña actualizada exitosamente."}


@router.get("/security/blocked-ips", response_model=list[BlockedIPReport])
async def list_blocked_ips(admin: AdminDep, people: PeopleRepoDep) -> list[BlockedIPReport]:
    """[SOLO ADMIN] IPs bloqueadas dentro de la Villa del Administrador."""
    return await people.list_blocked_ips(admin.villa_id)


@router.delete("/security/blocked-ips/{ip_address}")
async def unblock_ip(ip_address: str, request: Request, admin: AdminDep, people: PeopleRepoDep) -> dict[str, str]:
    """[SOLO ADMIN] Desbloquea una dirección IP dentro de su Villa."""
    if not await people.unblock_ip(admin.villa_id, ip_address):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"La IP '{ip_address}' no se encuentra bloqueada en {admin.villa_id}.")
    log_audit_event(
        event="SECURITY_IP_UNBLOCKED",
        actor=f"{admin.villa_id}:{admin.username}",
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
        details={"villa_id": admin.villa_id, "unblocked_ip": ip_address},
    )
    return {"message": f"La dirección IP {ip_address} ha sido desbloqueada en {admin.villa_id}."}
