from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Optika Timka"
    environment: str = "local"
    database_url: str = (
        "postgresql+psycopg://optika_timka:optika_timka@localhost:5432/optika_timka"
    )
    auth_secret: str = "change-me-local-auth-secret"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
