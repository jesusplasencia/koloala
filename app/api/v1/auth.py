import hmac
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.audit import get_client_ip, get_user_agent, log_audit_event
from app.core.config import settings
from app.core.security import (
    AdminDep,
    CurrentIdentityDep,
    VillaIdDep,
    create_access_token,
    generate_initial_lot_password,
    hash_password,
    verify_password,
)
from app.core.pii import normalize_doc, normalize_document_id
from app.core.whatsapp import send_whatsapp_pin_notification
from app.db.repositories import PostgresLotRepository, PostgresUserRepository
from app.db.session import AsyncSessionLocal
from app.models.lot import (
    normalize_lot_id,
    normalize_peru_phone,
    normalize_phone_username,
)
from app.models.user import (
    ActivateLotPinRequest,
    ActivateLotPinResponse,
    BlockedIPReport,
    ChangePasswordRequest,
    LoginRequest,
    MembershipRole,
    TokenResponse,
    UserLookupRequest,
    UserLookupResponse,
    UserLookupStatus,
    UserRole,
)
from app.repositories.lots_repo import LotRepositoryProtocol, in_memory_lot_repo
from app.repositories.users_repo import UserRepositoryProtocol, in_memory_user_repo

router = APIRouter(prefix="/auth", tags=["Authentication & Security"])


async def get_user_repo() -> AsyncIterator[UserRepositoryProtocol]:
    if not settings.use_postgres:
        yield in_memory_user_repo
        return
    async with AsyncSessionLocal() as session:
        try:
            yield PostgresUserRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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


UserRepoDep = Annotated[UserRepositoryProtocol, Depends(get_user_repo)]
LotRepoDep = Annotated[LotRepositoryProtocol, Depends(get_lot_repo)]


@router.post("/lookup", response_model=UserLookupResponse)
async def lookup_user_login_state(
    payload: UserLookupRequest,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
    lot_repo: LotRepoDep,
) -> UserLookupResponse:
    """
    Paso 1 del login: recibe el celular de 9 dígitos (o un usuario de staff como GUARDIA1).
    - READY_FOR_PASSWORD: ya tiene PIN/contraseña activa.
    - NEEDS_DOCUMENT: está en el padrón (ANCHOR/HOUSEHOLD) y aún no activa su cuenta; el siguiente
      paso le pide su DNI/CE antes de enviar el PIN por WhatsApp (anti-spam).
    - UNREGISTERED_NO_ACCOUNT: los visitantes no crean cuenta.
    SEGURIDAD: nunca devuelve nombre, rol ni lotes antes de autenticar (evita enumerar vecinos).
    """
    norm_username = normalize_lot_id(payload.username)
    user = await user_repo.get_user(villa_id, norm_username)
    memberships = await lot_repo.list_memberships_by_phone(villa_id, norm_username)

    if user and (
        user.last_login_at is not None
        or user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.GUARD)
    ):
        return UserLookupResponse(
            villa_id=villa_id, username=norm_username, status=UserLookupStatus.READY_FOR_PASSWORD
        )

    if memberships:
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.NEEDS_DOCUMENT,
            message="Confirma tu DNI o Carnet de Extranjería para recibir tu PIN por WhatsApp.",
        )

    if user:
        return UserLookupResponse(
            villa_id=villa_id, username=norm_username, status=UserLookupStatus.READY_FOR_PASSWORD
        )

    if norm_username.isdigit() and len(norm_username) == 9:
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.UNREGISTERED_NO_ACCOUNT,
            message=(
                "Este número no tiene cuenta. Si visitas una casa, pide a tu anfitrión "
                "su QR; si vas a un negocio o área común, acércate a garita con tu DNI."
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"El usuario '{norm_username}' no está registrado en {villa_id}.",
    )


@router.post("/activate-pin", response_model=ActivateLotPinResponse)
async def activate_lot_pin_via_whatsapp(
    payload: ActivateLotPinRequest,
    request: Request,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
    lot_repo: LotRepoDep,
) -> ActivateLotPinResponse:
    """
    Primer ingreso de un ANCHOR/HOUSEHOLD precargado:
    1. Confirma su DNI/CE contra el registrado por el Admin o su ANCHOR (comparación en tiempo constante).
    2. Máximo `max_pin_sends_per_phone_per_day` envíos por número cada 24 h, aunque cambie la IP.
    3. Genera un PIN de 8 dígitos en RAM (solo se guarda su hash) y lo envía por WhatsApp.
    Los DNI incorrectos cuentan para el bloqueo de IP (3 fallos).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    phone = normalize_phone_username(payload.username) or ""
    actor_label = f"{villa_id}:{phone}"

    blocked_report = await user_repo.is_ip_blocked(villa_id, client_ip)
    if blocked_report:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa "
                f"({blocked_report.attack_type})."
            ),
        )

    try:
        provided_doc = normalize_document_id(payload.document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    existing_user = await user_repo.get_user(villa_id, phone)
    if existing_user and existing_user.last_login_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta cuenta ya tiene un PIN activo. Ingresa con tu PIN.",
        )

    memberships = await lot_repo.list_memberships_by_phone(villa_id, phone)
    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este número no está registrado en el padrón de la Villa.",
        )

    since = datetime.now(UTC) - timedelta(hours=24)
    if await user_repo.count_pin_requests(villa_id, phone, since) >= settings.max_pin_sends_per_phone_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ya enviamos tu PIN varias veces hoy. Revisa tu WhatsApp o inténtalo mañana.",
        )

    registered_docs = {
        normalize_doc(m.document_id) for m in memberships if m.document_id and normalize_doc(m.document_id)
    }
    if not registered_docs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aún no tienes un DNI registrado. Pide al Administrador o al titular de tu lote que lo registre.",
        )

    matches = any(hmac.compare_digest(provided_doc or "", d or "") for d in registered_docs)
    if not matches:
        await user_repo.record_pin_request(villa_id, phone, client_ip, ok=False)
        failure_report = await user_repo.record_login_failure(
            villa_id=villa_id,
            attempted_username=phone,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        log_audit_event(
            event="AUTH_DOCUMENT_ACTIVATION_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={"villa_id": villa_id, "attempt": failure_report.failed_attempts},
        )
        if failure_report.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Los datos no coinciden con los registrados "
                f"(intento {failure_report.failed_attempts} de {settings.max_failed_attempts_per_ip})."
            ),
        )

    primary_mem = memberships[0]
    target_lot_id = primary_mem.lot_id
    owner_name = primary_mem.full_name

    fresh_pin = generate_initial_lot_password()
    await user_repo.upsert_user_account(
        villa_id=villa_id,
        username=phone,
        password_hash=hash_password(fresh_pin),
        role=UserRole.RESIDENT,
        lot_id=target_lot_id,
        full_name=owner_name,
        document_id=provided_doc,
    )

    sent_ok, channel = await send_whatsapp_pin_notification(
        phone=normalize_peru_phone(phone) or f"51{phone}",
        villa_id=villa_id,
        lot_id=target_lot_id,
        owner_name=owner_name,
        pin_password=fresh_pin,
    )
    if not sent_ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo despachar el mensaje de WhatsApp ({channel}).",
        )
    await user_repo.record_pin_request(villa_id, phone, client_ip, ok=True)

    log_audit_event(
        event="AUTH_PIN_ACTIVATED_VIA_WHATSAPP",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
        details={"villa_id": villa_id, "lot_id": target_lot_id, "channel": channel},
    )
    return ActivateLotPinResponse(
        villa_id=villa_id,
        lot_id=target_lot_id,
        delivery_channel=channel,
        message="Te enviamos tu PIN de 8 dígitos por WhatsApp.",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
    lot_repo: LotRepoDep,
) -> TokenResponse:
    """
    Autentica por número telefónico ('916220518', '934253226', '980180219') o código ('B14', 'GUARDIA1').
    Soporta una sola contraseña global por persona a través de todas las Villas (lasvillas.club)
    y resuelve automáticamente sus lotes y rol (ANCHOR / HOUSEHOLD) en la Villa actual.
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    norm_username = normalize_lot_id(payload.username)
    actor_label = f"{villa_id}:{norm_username}"

    # 1. Verificar si la IP del cliente ya se encuentra bloqueada en esta Villa
    blocked_report = await user_repo.is_ip_blocked(villa_id, client_ip)
    if blocked_report:
        log_audit_event(
            event="AUTH_BLOCKED_IP_ATTEMPT",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details=blocked_report.model_dump(mode="json"),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa "
                f"({blocked_report.attack_type}). Este incidente y tu IP ({client_ip}) "
                f"han sido reportados a la Administración de {villa_id}."
            ),
        )

    # 2. Buscar usuario (en la Villa o por su identidad telefónica global) y validar contraseña
    user = await user_repo.get_user(villa_id, norm_username)
    if not user or not verify_password(payload.password, user.password_hash):
        failure_report = await user_repo.record_login_failure(
            villa_id=villa_id,
            attempted_username=norm_username,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        if failure_report.is_blocked:
            log_audit_event(
                event="SECURITY_IP_BLOCKED_BRUTE_FORCE",
                actor=actor_label,
                client_ip=client_ip,
                user_agent=user_agent,
                details=failure_report.model_dump(mode="json"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos "
                    f"({failure_report.attack_type}). Este incidente y tu IP ({client_ip}) "
                    f"han sido reportados a la Administración de {villa_id}."
                ),
            )

        log_audit_event(
            event="AUTH_LOGIN_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": villa_id,
                "attempt": failure_report.failed_attempts,
                "max_attempts": settings.max_failed_attempts_per_ip,
                "attempted_usernames": failure_report.attempted_usernames,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Credenciales inválidas en {villa_id} (Intento {failure_report.failed_attempts} "
                f"de {settings.max_failed_attempts_per_ip})."
            ),
        )

    # Si un usuario de staff local (ADMIN o GUARD, NO SUPER_ADMIN) pertenece a otra Villa, no puede entrar aquí
    if (
        user.villa_id != villa_id
        and user.role in (UserRole.ADMIN, UserRole.GUARD)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tu cuenta administrativa/garita pertenece a {user.villa_id}, no a {villa_id}.",
        )

    # 3. Resolver membresías 3NF en la Villa solicitada (soporta multi-villa con 1 sola clave)
    memberships = await lot_repo.list_memberships_by_phone(villa_id, user.username)
    available_lots = [m.lot_id for m in memberships]
    primary_mem = memberships[0] if memberships else None

    if primary_mem:
        effective_lot_id = primary_mem.lot_id
        effective_mem_role: MembershipRole | None = primary_mem.membership_role
        effective_name = primary_mem.full_name
        effective_role = (
            user.role
            if user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN)
            else UserRole.RESIDENT
        )
    elif user.villa_id == villa_id and user.lot_id:
        effective_lot_id = user.lot_id
        effective_mem_role = MembershipRole.ANCHOR
        effective_name = user.full_name
        effective_role = user.role
        available_lots = [user.lot_id]
    elif user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.GUARD):
        effective_lot_id = None
        effective_mem_role = None
        effective_name = user.full_name
        effective_role = user.role
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes membresía activa (ANCHOR ni HOUSEHOLD) en esta Villa.",
        )

    # 4. Login exitoso: guardar IP para correlación futura y emitir JWT de 30 días sellado para esta Villa
    await user_repo.record_login_success(villa_id, user.username, client_ip)
    token = create_access_token(
        username=user.username,
        role=effective_role,
        villa_id=villa_id,
        lot_id=effective_lot_id,
        membership_role=effective_mem_role,
        full_name=effective_name,
    )

    log_audit_event(
        event="AUTH_LOGIN_SUCCESS",
        actor=f"{villa_id}:{user.username}",
        client_ip=client_ip,
        user_agent=user_agent,
        details={
            "villa_id": villa_id,
            "role": str(effective_role),
            "lot_id": effective_lot_id,
            "membership_role": str(effective_mem_role) if effective_mem_role else None,
            "available_lots": available_lots,
        },
    )

    return TokenResponse(
        access_token=token,
        expires_in_days=settings.access_token_expire_minutes // (60 * 24),
        villa_id=villa_id,
        username=user.username,
        role=effective_role,
        lot_id=effective_lot_id,
        membership_role=effective_mem_role,
        full_name=effective_name,
        available_lots=available_lots,
    )


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    identity: CurrentIdentityDep,
    user_repo: UserRepoDep,
) -> dict[str, str]:
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    actor_label = f"{identity.villa_id}:{identity.username}"

    user = await user_repo.get_user(identity.villa_id, identity.username)
    if not user or not verify_password(payload.current_password, user.password_hash):
        log_audit_event(
            event="AUTH_PASSWORD_CHANGE_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual ingresada no es correcta.",
        )

    await user_repo.update_password(
        identity.villa_id, identity.username, hash_password(payload.new_password)
    )
    log_audit_event(
        event="AUTH_PASSWORD_CHANGED",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return {"message": "Contraseña actualizada exitosamente."}


@router.get("/security/blocked-ips", response_model=list[BlockedIPReport])
async def list_blocked_ips(
    admin: AdminDep,
    user_repo: UserRepoDep,
) -> list[BlockedIPReport]:
    """
    [SOLO ADMIN] Lista las IPs bloqueadas exclusivamente dentro de la Villa del Administrador.
    """
    return await user_repo.list_blocked_ips(admin.villa_id)


@router.delete("/security/blocked-ips/{ip_address}")
async def unblock_ip(
    ip_address: str,
    request: Request,
    admin: AdminDep,
    user_repo: UserRepoDep,
) -> dict[str, str]:
    """[SOLO ADMIN] Desbloquea una dirección IP dentro de su Villa."""
    unblocked = await user_repo.unblock_ip(admin.villa_id, ip_address)
    if not unblocked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La IP '{ip_address}' no se encuentra bloqueada en {admin.villa_id}.",
        )
    log_audit_event(
        event="SECURITY_IP_UNBLOCKED",
        actor=f"{admin.villa_id}:{admin.username}",
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
        details={"villa_id": admin.villa_id, "unblocked_ip": ip_address},
    )
    return {
        "message": f"La dirección IP {ip_address} ha sido desbloqueada en {admin.villa_id}."
    }
