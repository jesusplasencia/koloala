from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.audit import get_client_ip, get_user_agent, log_audit_event
from app.core.config import settings
from app.core.security import (
    AdminDep,
    CurrentIdentityDep,
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
    user_repo: UserRepoDep,
) -> TokenResponse:
    """
    Autentica a residentes (ej. 'B14', 'U28'), vigilantes ('guardia1') o administrador ('admin').
    Protegido contra Fuerza Bruta y Password Spraying: al 3er fallo desde la misma IP,
    bloquea la IP e identifica si pertenece a algún vecino de la Villa.
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    norm_username = normalize_lot_id(payload.username)

    # 1. Verificar si la IP del cliente ya se encuentra bloqueada
    blocked_report = await user_repo.is_ip_blocked(client_ip)
    if blocked_report:
        log_audit_event(
            event="AUTH_BLOCKED_IP_ATTEMPT",
            actor=norm_username,
            client_ip=client_ip,
            user_agent=user_agent,
            details=blocked_report.model_dump(mode="json"),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Tu dirección IP ha sido bloqueada por actividad sospechosa "
                f"({blocked_report.attack_type}). Este incidente y tu IP ({client_ip}) "
                "han sido reportados al Administrador de Villa Club 3."
            ),
        )

    # 2. Buscar usuario y validar contraseña
    user = await user_repo.get_user(norm_username)
    if not user or not verify_password(payload.password, user.password_hash):
        failure_report = await user_repo.record_login_failure(
            attempted_username=norm_username,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        if failure_report.is_blocked:
            log_audit_event(
                event="SECURITY_IP_BLOCKED_BRUTE_FORCE",
                actor=norm_username,
                client_ip=client_ip,
                user_agent=user_agent,
                details=failure_report.model_dump(mode="json"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Tu dirección IP ha sido bloqueada tras 3 intentos fallidos "
                    f"({failure_report.attack_type}). Este incidente y tu IP ({client_ip}) "
                    "han sido reportados al Administrador de Villa Club 3."
                ),
            )

        log_audit_event(
            event="AUTH_LOGIN_FAILED",
            actor=norm_username,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "attempt": failure_report.failed_attempts,
                "max_attempts": settings.max_failed_attempts_per_ip,
                "attempted_usernames": failure_report.attempted_usernames,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Credenciales inválidas (Intento {failure_report.failed_attempts} "
                f"de {settings.max_failed_attempts_per_ip})."
            ),
        )

    # 3. Login exitoso: guardar IP para correlación futura y emitir JWT de 30 días
    await user_repo.record_login_success(user.username, client_ip)
    token = create_access_token(
        username=user.username,
        role=user.role,
        lot_id=user.lot_id,
    )

    log_audit_event(
        event="AUTH_LOGIN_SUCCESS",
        actor=user.username,
        client_ip=client_ip,
        user_agent=user_agent,
        details={"role": str(user.role), "lot_id": user.lot_id},
    )

    return TokenResponse(
        access_token=token,
        expires_in_days=settings.access_token_expire_minutes // (60 * 24),
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
    """
    Opcional: Permite a los propietarios cambiar su contraseña inicial si lo desean.
    (Las personas mayores pueden seguir usando su contraseña inicial sin caducidad).
    """
    client_ip = get_client_ip(request)
    user_agent = get_user_agent(request)

    user = await user_repo.get_user(identity.username)
    if not user or not verify_password(payload.current_password, user.password_hash):
        log_audit_event(
            event="AUTH_PASSWORD_CHANGE_FAILED",
            actor=identity.username,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual ingresada no es correcta.",
        )

    await user_repo.update_password(identity.username, hash_password(payload.new_password))
    log_audit_event(
        event="AUTH_PASSWORD_CHANGED",
        actor=identity.username,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return {"message": "Contraseña actualizada exitosamente."}


@router.get("/security/blocked-ips", response_model=list[BlockedIPReport])
async def list_blocked_ips(
    _admin: AdminDep,
    user_repo: UserRepoDep,
) -> list[BlockedIPReport]:
    """
    [SOLO ADMIN] Muestra todas las IPs bloqueadas por Fuerza Bruta o Password Spraying,
    incluyendo qué lotes de la Villa iniciaron sesión antes desde esa misma IP.
    """
    return await user_repo.list_blocked_ips()


@router.delete("/security/blocked-ips/{ip_address}")
async def unblock_ip(
    ip_address: str,
    request: Request,
    admin: AdminDep,
    user_repo: UserRepoDep,
) -> dict[str, str]:
    """[SOLO ADMIN] Desbloquea una dirección IP bloqueada."""
    unblocked = await user_repo.unblock_ip(ip_address)
    if not unblocked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La IP '{ip_address}' no se encuentra en la lista de bloqueos.",
        )
    log_audit_event(
        event="SECURITY_IP_UNBLOCKED",
        actor=admin.username,
        client_ip=get_client_ip(request),
        user_agent=get_user_agent(request),
        details={"unblocked_ip": ip_address},
    )
    return {"message": f"La dirección IP {ip_address} ha sido desbloqueada."}
