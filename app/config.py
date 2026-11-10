from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Optika Timka"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://optika_timka:optika_timka@localhost:5432/optika_timka"
    auth_secret: SecretStr = SecretStr("change-me-local-auth-secret")
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
    public_base_url: str | None = None
    phone_auth_debug: bool = False
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"local", "test", "production"}:
            raise ValueError("ENVIRONMENT must be local, test, or production")
        return value

    @field_validator(
        "admin_password",
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "public_base_url",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_security(self) -> Self:
        if self.public_base_url:
            origin = urlsplit(self.public_base_url)
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
            ):
                raise ValueError("PUBLIC_BASE_URL must be an HTTP(S) origin without a path")
            self.public_base_url = self.public_base_url.rstrip("/")
        if bool(self.google_client_id) != bool(self.google_client_secret):
            raise ValueError("Configure both Google OAuth credentials, or neither")
        if self.environment == "production":
            if len(self.auth_secret.get_secret_value()) < 32:
                raise ValueError("Production AUTH_SECRET must contain at least 32 characters")
            if not self.admin_password or len(self.admin_password.get_secret_value()) < 16:
                raise ValueError("Production ADMIN_PASSWORD must contain at least 16 characters")
            if not self.public_base_url or not self.public_base_url.startswith("https://"):
                raise ValueError("Production PUBLIC_BASE_URL must use HTTPS")
            if self.phone_auth_debug:
                raise ValueError("Debug phone authentication is forbidden in production")
            if self.google_client_id:
                expected = f"{self.public_base_url}/auth/google/callback"
                if self.google_redirect_uri and self.google_redirect_uri != expected:
                    raise ValueError("GOOGLE_REDIRECT_URI must match PUBLIC_BASE_URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
