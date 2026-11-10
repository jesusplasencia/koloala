from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _ensure_asyncpg_scheme(url: str) -> str:
    """
    Permite pegar directamente la URI de Supabase/RDS ('postgresql://...' o 'postgres://...')
    en el .env y la convierte automáticamente a 'postgresql+asyncpg://...'.
    """
    cleaned = url.strip()
    if cleaned.startswith("postgres://"):
        return "postgresql+asyncpg://" + cleaned[len("postgres://") :]
    if cleaned.startswith("postgresql://"):
        return "postgresql+asyncpg://" + cleaned[len("postgresql://") :]
    return cleaned


class Settings(BaseSettings):
    app_name: str = "Koloala - Villa Club 3 Motor de Garita"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    # Reglas del Negocio Parametrizables (TTL en minutos)
    delivery_pass_ttl_minutes: int = 45
    taxi_pass_ttl_minutes: int = 30
    guest_pass_ttl_minutes: int = 480

    # Autenticación JWT y Seguridad Anti-Fuerza Bruta / Password Spraying
    jwt_secret_key: str = "vc3-koloala-super-secret-jwt-key-change-in-production-2026"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 43200  # 30 días (43,200 minutos)
    max_failed_attempts_per_ip: int = 3

    # Base de Datos PostgreSQL (SQLAlchemy 2.0 Async + Separación de Roles Least-Privilege)
    # - use_postgres=False usa InMemory en desarrollo rápido y pytest unitarios sin Docker.
    # - use_postgres=True usa PostgreSQL real (Docker local, Supabase o AWS RDS).
    use_postgres: bool = False
    # URL de Runtime para el contenedor FastAPI (SOLO permisos SELECT, INSERT, UPDATE)
    database_url: str = (
        "postgresql+asyncpg://koloala_runtime:koloala_runtime_secret@127.0.0.1:5432/koloala"
    )
    # URL de Migración exclusiva para Alembic CLI (Dueño DDL con permisos CREATE/ALTER TABLE)
    migration_database_url: str = (
        "postgresql+asyncpg://postgres:postgres_admin_secret@127.0.0.1:5432/koloala"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5

    @field_validator("database_url", "migration_database_url", mode="before")
    @classmethod
    def _normalize_db_urls(cls, value: str) -> str:
        return _ensure_asyncpg_scheme(value)

    # Auditoría Forense Incremental (.jsonl) y Archivado S3
    audit_log_dir: str = "logs"
    # En PC local (sin S3), conserva solo los últimos 5 .gz y borra los viejos
    max_local_gz_files: int = 5
    s3_audit_bucket: str | None = None
    s3_audit_prefix: str = "audit-logs"
    # Glacier Instant Retrieval (lectura máx 1 vez/semana)
    s3_audit_storage_class: str = "GLACIER_IR"
    aws_region: str = "us-east-1"

    # Notificaciones WhatsApp vía Evolution API (con fallback automático a consola en local)
    evolution_api_url: str | None = None  # Ej: http://localhost:8080 o https://evo.lasvillas.club
    evolution_api_key: str | None = None  # Global API Key o Instance Token de Evolution API
    evolution_instance_name: str = "lasvillas"  # Nombre de la instancia vinculada por QR

    # CORS para el Frontend PWA (fololala en Vite local + subdominios *.lasvillas.club)
    cors_allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://vc1.localhost:5173",
        "http://vc2.localhost:5173",
        "http://vc3.localhost:5173",
        "https://lasvillas.club",
        "https://vc1.lasvillas.club",
        "https://vc2.lasvillas.club",
        "https://vc3.lasvillas.club",
    ]
    cors_allow_origin_regex: str = (
        r"^https?://([a-z0-9-]+\.)?(lasvillas\.club|vercel\.app|pages\.dev|up\.railway\.app|localhost|127\.0\.0\.1)(:\d+)?$"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
