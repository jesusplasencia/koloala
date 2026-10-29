from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.audit import get_client_ip, get_user_agent, log_audit_event
from app.core.config import settings
from app.core.security import (
    AdminDep,
    CurrentIdentityDep,
    VillaIdDep,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.lot import normalize_lot_id
from app.models.user import (
    BlockedIPReport,
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
)
from app.repositories.users_repo import UserRepositoryProtocol, in_memory_user_repo

router = APIRouter(prefix="/auth", tags=["Authentication & Security"])


def get_user_repo() -> UserRepositoryProtocol:
    return in_memory_user_repo


UserRepoDep = Annotated[UserRepositoryProtocol, Depends(get_user_repo)]


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
