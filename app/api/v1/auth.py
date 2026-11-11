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
    RegisterVisitorRequest,
    RegisterVisitorResponse,
    TokenResponse,
    UserLookupRequest,
    UserLookupResponse,
    UserLookupStatus,
    UserRole,
)
from app.models.villa_services import (
    VISITOR_SERVICES_ENABLED,
    find_villa_service,
    list_villa_services,
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


@router.get("/villa-services", include_in_schema=False)
async def get_villa_services(villa_id: VillaIdDep) -> list[dict[str, str]]:
    """[SHADOW] Catálogo de servicios internos. Deshabilitado hasta activar VISITOR_SERVICES_ENABLED."""
    if not VISITOR_SERVICES_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return [s.model_dump() for s in list_villa_services(villa_id)]


@router.post("/lookup", response_model=UserLookupResponse)
async def lookup_user_login_state(
    payload: UserLookupRequest,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
    lot_repo: LotRepoDep,
) -> UserLookupResponse:
    """
    Paso 1 del Login Inteligente: recibe el número de celular ('916220518', '980180219')
    o el código de lote/usuario ('P21', 'ADMIN').
    - Si ya tiene PIN activo (last_login_at != None), devuelve READY_FOR_PASSWORD.
    - Si está precargado como ANCHOR o HOUSEHOLD en la tabla 3NF lot_memberships (o en property_lots)
      y aún nunca ha iniciado sesión, devuelve NEEDS_PHONE_ACTIVATION.
    - Si no tiene cuenta, devuelve UNREGISTERED_OFFER_VISITOR para iniciar su onboarding
      como VISITOR (Nombre + DNI/CE). Nunca se expone el teléfono de Admin ni residentes.
    """
    norm_username = normalize_lot_id(payload.username)
    user = await user_repo.get_user(villa_id, norm_username)
    lot = await lot_repo.get_lot(villa_id, norm_username)
    memberships = await lot_repo.list_memberships_by_phone(villa_id, norm_username)

    available_lots = [m.lot_id for m in memberships]
    primary_membership = memberships[0] if memberships else None

    # 1. Si el usuario existe y ya inició sesión (o es cuenta de staff activa) -> pide su PIN
    if user and (
        user.last_login_at is not None
        or user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.GUARD)
    ):
        mem_role = (
            primary_membership.membership_role
            if primary_membership
            else (MembershipRole.ANCHOR if user.lot_id else None)
        )
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.READY_FOR_PASSWORD,
            owner_name=(
                primary_membership.full_name
                if primary_membership
                else (user.full_name or (lot.owner_name if lot else None))
            ),
            role=user.role,
            membership_role=mem_role,
            available_lots=available_lots or ([user.lot_id] if user.lot_id else []),
        )

    # 2. Si es un teléfono precargado en lot_memberships (ANCHOR o HOUSEHOLD) que aún no tiene PIN activo
    #    (no se devuelve pista de dígitos: el propio celular ES el username)
    if primary_membership:
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.NEEDS_PHONE_ACTIVATION,
            owner_name=primary_membership.full_name,
            role=UserRole.RESIDENT,
            membership_role=primary_membership.membership_role,
            available_lots=available_lots,
        )

    # 3. Compatibilidad lote directo ('P21'): lote cargado en padrón con celular pero sin primer login
    if lot and lot.owner_phone and not (user and user.last_login_at):
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.NEEDS_PHONE_ACTIVATION,
            owner_name=lot.owner_name,
            role=UserRole.RESIDENT,
            membership_role=MembershipRole.ANCHOR,
            available_lots=[lot.lot_id],
        )

    if user or lot:
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.READY_FOR_PASSWORD,
            owner_name=lot.owner_name if lot else (user.full_name if user else None),
            role=user.role if user else UserRole.RESIDENT,
        )

    # 4. Celular de 9 dígitos sin cuenta (no es ANCHOR, HOUSEHOLD ni VISITOR):
    # se asume VISITOR y se inicia su onboarding (Nombre + DNI/CE).
    # SEGURIDAD: jamás se expone el teléfono del Administrador ni de residentes a desconocidos.
    if norm_username.isdigit() and len(norm_username) == 9:
        return UserLookupResponse(
            villa_id=villa_id,
            username=norm_username,
            status=UserLookupStatus.UNREGISTERED_OFFER_VISITOR,
            role=UserRole.VISITOR,
            visitor_onboarding_message=(
                f"Bienvenido a {villa_id}. Para ingresar regístrate con tu nombre "
                "y tu DNI o Carnet de Extranjería."
            ),
            available_services=(
                [s.model_dump() for s in list_villa_services(villa_id)]
                if VISITOR_SERVICES_ENABLED
                else []
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"El lote o usuario '{norm_username}' no está registrado en {villa_id}.",
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
    Paso 1B (Activación Just-In-Time en 1er Ingreso para ANCHOR o HOUSEHOLD):
    Verifica que el número de WhatsApp coincida con la membresía 3NF o padrón del lote.
    Si falla 3 veces, bloquea la IP (Anti-Fuerza Bruta).
    Si coincide, genera un PIN numérico de 8 dígitos en memoria RAM, guarda SOLO su
    hash criptográfico en la base de datos y envía el PIN por WhatsApp (Evolution API).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    norm_username = normalize_lot_id(payload.username)
    actor_label = f"{villa_id}:{norm_username}"

    blocked_report = await user_repo.is_ip_blocked(villa_id, client_ip)
    if blocked_report:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu dirección IP ha sido bloqueada en {villa_id} por actividad sospechosa "
                f"({blocked_report.attack_type})."
            ),
        )

    memberships = await lot_repo.list_memberships_by_phone(villa_id, norm_username)
    lot = await lot_repo.get_lot(villa_id, norm_username)

    # Solo se permite la activación inicial: si la cuenta ya tiene PIN activo, cualquiera que
    # conozca el número podría forzar resets de PIN repetidos (DoS). Debe ingresar con su PIN
    # o pedir reseteo al Administrador.
    existing_user = await user_repo.get_user(villa_id, norm_username)
    if existing_user and existing_user.last_login_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta cuenta ya tiene un PIN activo. Ingresa con tu PIN.",
        )

    if memberships:
        primary_mem = memberships[0]
        expected_phone = normalize_peru_phone(primary_mem.phone)
        target_lot_id = primary_mem.lot_id
        owner_name = primary_mem.full_name
        target_email = None
    elif lot and lot.owner_phone:
        expected_phone = normalize_peru_phone(lot.owner_phone)
        target_lot_id = lot.lot_id
        owner_name = lot.owner_name
        target_email = lot.owner_email
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"El usuario o lote {norm_username} no tiene un número de WhatsApp registrado "
                "en la Junta Directiva. Solicita al Administrador actualizar tu "
                "teléfono en el padrón."
            ),
        )

    provided_phone = normalize_peru_phone(payload.phone)

    if not expected_phone or expected_phone != provided_phone:
        failure_report = await user_repo.record_login_failure(
            villa_id=villa_id,
            attempted_username=norm_username,
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
                "username": norm_username,
                "attempt": failure_report.failed_attempts,
            },
        )
        if failure_report.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Tu dirección IP ha sido bloqueada en {villa_id} tras 3 intentos fallidos "
                    "al verificar el teléfono."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"El número de WhatsApp no coincide con el registrado para {norm_username} "
                f"(Intento {failure_report.failed_attempts} de {settings.max_failed_attempts_per_ip})."
            ),
        )

    # Generación Just-In-Time en memoria RAM: jamás se almacena en texto plano
    fresh_pin = generate_initial_lot_password()
    pin_hash = hash_password(fresh_pin)

    await user_repo.upsert_user_account(
        villa_id=villa_id,
        username=norm_username,
        password_hash=pin_hash,
        role=UserRole.RESIDENT,
        lot_id=target_lot_id,
        full_name=owner_name,
        email=target_email,
    )

    # Si se activó por código de lote ('P21') y tiene owner_phone, sincronizar también el usuario telefónico
    local_owner_phone = normalize_phone_username(expected_phone)
    if local_owner_phone and local_owner_phone != norm_username:
        await user_repo.upsert_user_account(
            villa_id=villa_id,
            username=local_owner_phone,
            password_hash=pin_hash,
            role=UserRole.RESIDENT,
            lot_id=target_lot_id,
            full_name=owner_name,
            email=target_email,
        )

    sent_ok, channel = await send_whatsapp_pin_notification(
        phone=expected_phone,
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

    log_audit_event(
        event="AUTH_LOT_PIN_ACTIVATED_VIA_WHATSAPP",
        actor=actor_label,
        client_ip=client_ip,
        user_agent=user_agent,
        details={
            "villa_id": villa_id,
            "username": norm_username,
            "lot_id": target_lot_id,
            "channel": channel,
        },
    )

    return ActivateLotPinResponse(
        villa_id=villa_id,
        lot_id=target_lot_id,
        delivery_channel=channel,
        message=(
            f"Hemos enviado tu PIN de 8 dígitos al WhatsApp registrado "
            f"({norm_username} - Lote {target_lot_id})."
        ),
    )


@router.post(
    "/register-visitor",
    response_model=RegisterVisitorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_external_visitor(
    payload: RegisterVisitorRequest,
    request: Request,
    villa_id: VillaIdDep,
    user_repo: UserRepoDep,
    lot_repo: LotRepoDep,
) -> RegisterVisitorResponse:
    """
    Autoregistro con trazabilidad para visitantes externos o clientes de comercios internos
    (Panadería, Botica, Parque, Canchita):
    - Valida celular de 9 dígitos y DNI (8 dígitos) o Carnet de Extranjería (9-12 caracteres).
    - Genera PIN de 8 dígitos en RAM, guarda solo el hash y lo despacha por WhatsApp (Evolution API).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)

    norm_phone = normalize_phone_username(payload.phone)
    if not norm_phone or not norm_phone.isdigit() or len(norm_phone) != 9:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ingresa un número de celular válido de 9 dígitos (ej. 955443322).",
        )

    cleaned_doc = payload.document_id.strip().upper()
    if not (8 <= len(cleaned_doc) <= 12) or not cleaned_doc.isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ingresa un DNI válido (8 dígitos) o Carnet de Extranjería (9 a 12 caracteres).",
        )

    full_name = payload.full_name.strip()
    destination_code: str | None = None
    if VISITOR_SERVICES_ENABLED and payload.destination:
        service = find_villa_service(villa_id, payload.destination)
        if not service:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El servicio '{payload.destination}' no existe en {villa_id}.",
            )
        destination_code = service.code

    # Un número que YA tiene cuenta (residente, staff o visitante) no puede re-registrarse:
    # evita que un tercero resetee el PIN de otra persona o suplante a un residente.
    existing = await user_repo.get_user(villa_id, norm_phone)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este número ya tiene una cuenta. Ingresa con tu PIN.",
        )
    # Si está precargado como ANCHOR/HOUSEHOLD debe activar su PIN como residente, no como visitante
    if await lot_repo.list_memberships_by_phone(villa_id, norm_phone):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este número ya está empadronado en la Villa. Activa tu PIN como residente.",
        )

    fresh_pin = generate_initial_lot_password()
    pin_hash = hash_password(fresh_pin)

    await user_repo.upsert_user_account(
        villa_id=villa_id,
        username=norm_phone,
        password_hash=pin_hash,
        role=UserRole.VISITOR,
        lot_id=None,
        full_name=full_name,
        document_id=cleaned_doc,
    )

    wa_phone = normalize_peru_phone(norm_phone) or f"51{norm_phone}"
    sent_ok, channel = await send_whatsapp_pin_notification(
        phone=wa_phone,
        villa_id=villa_id,
        lot_id="VISITANTE",
        owner_name=full_name,
        pin_password=fresh_pin,
    )
    if not sent_ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo despachar el PIN de visitante por WhatsApp ({channel}).",
        )

    log_audit_event(
        event="AUTH_VISITOR_REGISTERED",
        actor=f"{villa_id}:{norm_phone}",
        client_ip=client_ip,
        user_agent=user_agent,
        details={
            "villa_id": villa_id,
            "phone": norm_phone,
            "full_name": full_name,
            "document_id": cleaned_doc,
            "channel": channel,
        },
    )

    return RegisterVisitorResponse(
        villa_id=villa_id,
        username=norm_phone,
        full_name=full_name,
        document_id=cleaned_doc,
        role=UserRole.VISITOR,
        destination=destination_code,
        delivery_channel=channel,
        message=(
            f"Registro completado. Hemos enviado tu PIN de 8 dígitos por WhatsApp al {norm_phone} "
            "para que generes tu QR de ingreso."
        ),
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
    else:
        effective_lot_id = None
        effective_mem_role = None
        effective_name = user.full_name
        effective_role = (
            user.role
            if user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.GUARD)
            else UserRole.VISITOR
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
