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

    # Auditoría Forense Incremental (.jsonl) y Archivado S3
    audit_log_dir: str = "logs"
    s3_audit_bucket: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
