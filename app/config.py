from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.validation import normalize_email


class Settings(BaseSettings):
    app_name: str = "Optika Timka"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://optika_timka:optika_timka@localhost:5432/optika_timka"
    auth_secret: SecretStr = SecretStr("change-me-local-auth-secret")
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
    admin_google_email: str | None = None
    admin_google_subjects: Annotated[frozenset[str], NoDecode] = frozenset()
    public_base_url: str | None = None
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

    @field_validator("admin_google_email")
    @classmethod
    def validate_admin_google_email(cls, value: str | None) -> str | None:
        email = normalize_email(value)
        if email and (
            len(email) > 255
            or email.count("@") != 1
            or any(char.isspace() for char in email)
            or not all(email.split("@"))
        ):
            raise ValueError("ADMIN_GOOGLE_EMAIL must be a single email address")
        return email

    @field_validator("admin_google_subjects", mode="before")
    @classmethod
    def parse_admin_google_subjects(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(subject.strip() for subject in value.split(",")) if value.strip() else ()
        return value

    @field_validator("admin_google_subjects")
    @classmethod
    def validate_admin_google_subjects(cls, value: frozenset[str]) -> frozenset[str]:
        for subject in value:
            if not subject or len(subject) > 255 or not subject.isascii() or any(
                char.isspace() or not char.isprintable() or char == "," for char in subject
            ):
                raise ValueError("ADMIN_GOOGLE_SUBJECTS must contain valid Google account IDs")
        return value

    @property
    def google_admin_configured(self) -> bool:
        return bool(self.admin_google_email or self.admin_google_subjects)

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
        if self.google_admin_configured and not self.google_client_id:
            raise ValueError("Google administrator accounts require Google OAuth credentials")
        if self.environment == "production":
            auth_secret = self.auth_secret.get_secret_value()
            if len(auth_secret.strip()) < 32 or auth_secret.lower().startswith(("replace-", "change-me")):
                raise ValueError("Production AUTH_SECRET must contain at least 32 characters")
            password = self.admin_password.get_secret_value() if self.admin_password else ""
            if not self.google_admin_configured and (
                len(password.strip()) < 16 or password.lower().startswith(("replace-", "change-me"))
            ):
                raise ValueError("Production ADMIN_PASSWORD must contain at least 16 characters")
            database = make_url(self.database_url)
            if database.get_backend_name() != "postgresql":
                raise ValueError("Production requires a PostgreSQL database")
            # This is a rejected public development value, never an accepted credential.
            if database.password == "optika_timka":  # nosec B105
                raise ValueError("Replace the shared development PostgreSQL password in production")
            if not self.public_base_url or not self.public_base_url.startswith("https://"):
                raise ValueError("Production PUBLIC_BASE_URL must use HTTPS")
            if self.google_client_id:
                expected = f"{self.public_base_url}/auth/google/callback"
                if self.google_redirect_uri and self.google_redirect_uri != expected:
                    raise ValueError("GOOGLE_REDIRECT_URI must match PUBLIC_BASE_URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
