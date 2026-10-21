from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Koloala - Villa Club 3 Motor de Garita"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    # Reglas del Negocio Parametrizables (TTL en minutos)
    delivery_pass_ttl_minutes: int = 45
    taxi_pass_ttl_minutes: int = 30
    guest_pass_ttl_minutes: int = 480

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
