from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Auditoría Forense Incremental (.jsonl) y Archivado S3
    audit_log_dir: str = "logs"
    s3_audit_bucket: str | None = None
    s3_audit_prefix: str = "audit-logs"
    s3_audit_storage_class: str = "GLACIER_IR"  # Glacier Instant Retrieval (lectura máx 1 vez/semana)
    aws_region: str = "us-east-1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
