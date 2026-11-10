from collections.abc import AsyncIterator
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
from app.core.whatsapp import send_whatsapp_pin_notification
from app.db.repositories import PostgresLotRepository, PostgresUserRepository
from app.db.session import AsyncSessionLocal
from app.models.lot import normalize_lot_id, normalize_peru_phone
from app.models.user import (
    ActivateLotPinRequest,
    ActivateLotPinResponse,
    BlockedIPReport,
    ChangePasswordRequest,
    LoginRequest,
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
    Paso 1 del Login Inteligente: recibe únicamente el código de lote o usuario (ej. 'P21', 'P20', 'ADMIN').
    - Si es ADMIN, GUARD o un lote que ya inició sesión antes (last_login_at != None),
      devuelve READY_FOR_PASSWORD.
    - Si es un lote del padrón que NUNCA ha iniciado sesión (last_login_at is None) y tiene
      un teléfono registrado en el padrón, devuelve NEEDS_PHONE_ACTIVATION con una pista
      enmascarada ('••• 321') para que confirme su WhatsApp y reciba su PIN generado en el acto.
    """
    norm_username = normalize_lot_id(payload.username)
    user = await user_repo.get_user(villa_id, norm_username)
    lot = await lot_repo.get_lot(villa_id, norm_username)

    if not user and not lot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El lote o usuario '{norm_username}' no está registrado en {villa_id}.",
        )

    # Si es Guardia o Admin, o un vecino que ya inició sesión antes -> pide su PIN/contraseña
    if user and (user.role != UserRole.RESIDENT or user.last_login_at is not None):
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.READY_FOR_PASSWORD,
            owner_name=lot.owner_name if lot else None,
        )

    # Si es un lote cargado en padrón/CSV que aún nunca ha iniciado sesión y tiene celular registrado
    if lot and lot.owner_phone:
        norm_phone = normalize_peru_phone(lot.owner_phone) or ""
        last_digits = norm_phone[-3:] if len(norm_phone) >= 3 else norm_phone
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.NEEDS_PHONE_ACTIVATION,
            masked_phone_hint=f"••• {last_digits}",
            owner_name=lot.owner_name,
        )

    # Fallback: lote sin teléfono cargado en el padrón (usa su PIN actual directo)
    return UserLookupResponse(
        villa_id=villa_id,
        username=norm_username,
        status=UserLookupStatus.READY_FOR_PASSWORD,
        owner_name=lot.owner_name if lot else None,
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
    Paso 1B (Activación Just-In-Time en 1er Ingreso):
    Verifica que el número de WhatsApp ingresado coincida con el padrón del lote.
    Si falla 3 veces, bloquea la IP (Anti-Fuerza Bruta).
    Si coincide, genera un nuevo PIN numérico de 8 dígitos en memoria RAM, guarda SOLO su
    hash bcrypt en la base de datos y envía el PIN por WhatsApp (Evolution API).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    norm_lot_id = normalize_lot_id(payload.username)
    actor_label = f"{villa_id}:{norm_lot_id}"

    blocked_report = await user_repo.is_ip_blocked(villa_id, client_ip)
    if blocked_report:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa "
                f"({blocked_report.attack_type})."
            ),
        )

    lot = await lot_repo.get_lot(villa_id, norm_lot_id)
    if not lot or not lot.owner_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"El lote {norm_lot_id} no tiene un número de WhatsApp registrado "
                "en la Junta Directiva. Solicita al Administrador actualizar tu "
                "teléfono en el padrón."
            ),
        )

    expected_phone = normalize_peru_phone(lot.owner_phone)
    provided_phone = normalize_peru_phone(payload.phone)

    if not expected_phone or expected_phone != provided_phone:
        failure_report = await user_repo.record_login_failure(
            villa_id=villa_id,
            attempted_username=norm_lot_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        log_audit_event(
            event="AUTH_PHONE_ACTIVATION_FAILED",
            actor=actor_label,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "villa_id": villa_id,
                "lot_id": norm_lot_id,
                "attempt": failure_report.failed_attempts,
            },
        )
        if failure_report.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos "
                    "al verificar el teléfono del lote."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"El número de WhatsApp no coincide con el registrado para el lote {norm_lot_id} "
                f"(Intento {failure_report.failed_attempts} de {settings.max_failed_attempts_per_ip})."
            ),
        )

    # Generación Just-In-Time en memoria RAM: jamás se almacena en texto plano
    fresh_pin = generate_initial_lot_password()
    existing_user = await user_repo.get_user(villa_id, norm_lot_id)
    if existing_user:
        await user_repo.update_password(villa_id, norm_lot_id, hash_password(fresh_pin))
    else:
        await user_repo.create_or_get_resident_user(
            villa_id=villa_id,
            lot_id=norm_lot_id,
            initial_password=fresh_pin,
            email=lot.owner_email,
        )

    sent_ok, channel = await send_whatsapp_pin_notification(
        phone=expected_phone,
        villa_id=villa_id,
        lot_id=norm_lot_id,
        owner_name=lot.owner_name,
        pin_password=fresh_pin,
    )
    if not sent_ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo despachar el mensaje de WhatsApp ({channel}).",
        )

    log_audit_event(
        event="AUTH_LOT_PIN_ACTIVATED_VIA_WHATSAPP",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
        details={"villa_id": villa_id, "lot_id": norm_lot_id, "channel": channel},
    )

    return ActivateLotPinResponse(
        villa_id=villa_id,
        lot_id=norm_lot_id,
        delivery_channel=channel,
        message=(
            f"Hemos enviado tu PIN de 8 dígitos al WhatsApp registrado "
            f"del lote {norm_lot_id}."
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
) -> TokenResponse:
    """
    Autentica a residentes (ej. 'B14', 'P20'), vigilantes ('guardia1') o administrador ('admin')
    sectorizados por su Villa (detectada vía subdominio 'vc3.lasvillas.club' o 'X-Villa-Id').
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

    # 2. Buscar usuario dentro de la Villa y validar contraseña
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

    # 3. Login exitoso: guardar IP para correlación futura y emitir JWT de 30 días
    await user_repo.record_login_success(user.villa_id, user.username, client_ip)
    token = create_access_token(
        username=user.username,
        role=user.role,
        villa_id=user.villa_id,
        lot_id=user.lot_id,
    )

    log_audit_event(
        event="AUTH_LOGIN_SUCCESS",
        actor=f"{user.villa_id}:{user.username}",
        client_ip=client_ip,
        user_agent=user_agent,
        details={
            "villa_id": user.villa_id,
            "role": str(user.role),
            "lot_id": user.lot_id,
        },
    )

    return TokenResponse(
        access_token=token,
        expires_in_days=settings.access_token_expire_minutes // (60 * 24),
        villa_id=user.villa_id,
        username=user.username,
        role=user.role,
        lot_id=user.lot_id,
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
