import base64
import hashlib
import hmac
import json
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.models.user import UserRole

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    """Genera un hash PBKDF2-HMAC-SHA256 con salt aleatorio."""
    actual_salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        actual_salt.encode("utf-8"),
        100_000,
    )
    return f"pbkdf2_sha256${actual_salt}${dk.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verifica en tiempo constante si plain_password coincide con stored_hash."""
    try:
        algorithm, salt, _ = stored_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        expected_hash = hash_password(plain_password, salt=salt)
        return hmac.compare_digest(expected_hash, stored_hash)
    except ValueError:
        return False


def generate_initial_lot_password(lot_id: str) -> str:
    """
    Genera una contraseña inicial fácil de leer por WhatsApp para propietarios
    (incluyendo adultos mayores), ej: 'VC3-B14-8492'.
    """
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"VC3-{lot_id.upper()}-{digits}"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(
    username: str,
    role: UserRole,
    lot_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Crea un JWT firmado con HMAC-SHA256 válido por 30 días por defecto."""
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": username,
        "role": str(role),
        "lot_id": lot_id,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica y verifica firma y expiración de un token JWT HS256."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(
            settings.jwt_secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual_sig = _b64url_decode(signature_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise ValueError("Firma de token inválida.")

        payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        now_ts = int(datetime.now(UTC).timestamp())
        if payload.get("exp", 0) < now_ts:
            raise ValueError("El token ha expirado.")
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de sesión inválido o expirado. Inicie sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


class AuthenticatedIdentity:
    def __init__(self, username: str, role: UserRole, lot_id: str | None) -> None:
        self.username = username
        self.role = role
        self.lot_id = lot_id


async def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedIdentity:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado. Debe enviar su Bearer Token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    return AuthenticatedIdentity(
        username=payload["sub"],
        role=UserRole(payload["role"]),
        lot_id=payload.get("lot_id"),
    )


CurrentIdentityDep = Annotated[AuthenticatedIdentity, Depends(get_current_identity)]


async def require_resident_lot(identity: CurrentIdentityDep) -> str:
    """
    Asegura que quien llama al endpoint tenga un lot_id asignado (Residente)
    y retorna ese lot_id (ej. 'B14'), haciendo imposible falsificar el lote de otro vecino.
    """
    if identity.role != UserRole.RESIDENT or not identity.lot_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los propietarios/residentes con un lote asignado pueden generar pases de visita.",
        )
    return identity.lot_id


async def require_guard_or_admin(
    identity: CurrentIdentityDep,
) -> AuthenticatedIdentity:
    if identity.role not in (UserRole.GUARD, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido exclusivamente al personal de Garita (GUARD) o Administración (ADMIN).",
        )
    return identity


async def require_admin(identity: CurrentIdentityDep) -> AuthenticatedIdentity:
    if identity.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido exclusivamente al Administrador de Villa Club 3.",
        )
    return identity


ResidentLotDep = Annotated[str, Depends(require_resident_lot)]
GuardOrAdminDep = Annotated[AuthenticatedIdentity, Depends(require_guard_or_admin)]
AdminDep = Annotated[AuthenticatedIdentity, Depends(require_admin)]
