"""Environment-based configuration (secrets never live in the DB or the repo)."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    secret_key: str = Field(default="", alias="DOWNTUBE_SECRET_KEY")
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    base_url: str = Field(default="http://localhost:8092", alias="DOWNTUBE_BASE_URL")
    allowed_emails_raw: str = Field(default="", alias="DOWNTUBE_ALLOWED_EMAILS")
    admin_emails_raw: str = Field(default="", alias="DOWNTUBE_ADMIN_EMAILS")

    data_dir: Path = Field(default=Path("/data"), alias="DOWNTUBE_DATA_DIR")
    temp_dir: Path = Field(default=Path("/temp"), alias="DOWNTUBE_TEMP_DIR")
    log_dir: Path = Field(default=Path("/logs"), alias="DOWNTUBE_LOG_DIR")
    storage_mounts_raw: str = Field(default="/archive", alias="DOWNTUBE_STORAGE_MOUNTS")
    archive_root: str = Field(default="/archive", alias="DOWNTUBE_ARCHIVE_ROOT")
    host_archive_path: str | None = Field(default=None, alias="DOWNTUBE_HOST_ARCHIVE_PATH")

    cookie_secure: bool | None = Field(default=None, alias="DOWNTUBE_COOKIE_SECURE")
    # Reverse proxies (CIDRs) whose X-Real-IP / X-Forwarded-For / X-Forwarded-Proto are trusted.
    trusted_proxies_raw: str = Field(default="127.0.0.1/32", alias="DOWNTUBE_TRUSTED_PROXIES")
    log_level: str = Field(default="INFO", alias="DOWNTUBE_LOG_LEVEL")
    ytdlp_bin: str = Field(default="yt-dlp", alias="DOWNTUBE_YTDLP_BIN")
    ffprobe_bin: str = Field(default="ffprobe", alias="DOWNTUBE_FFPROBE_BIN")
    static_dir: Path | None = Field(default=None, alias="DOWNTUBE_STATIC_DIR")  # built SPA
    session_days: int = Field(default=14, alias="DOWNTUBE_SESSION_DAYS")
    # Disable background threads (tests / one-off CLI use).
    disable_background: bool = Field(default=False, alias="DOWNTUBE_DISABLE_BACKGROUND")

    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL, not a secret
    google_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def allowed_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails_raw.split(",") if e.strip()}

    @property
    def admin_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails_raw.split(",") if e.strip()}

    def is_allowed(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() in self.allowed_emails  # type: ignore[union-attr]

    @property
    def storage_mounts(self) -> list[Path]:
        return [Path(p.strip()) for p in self.storage_mounts_raw.split(",") if p.strip()]

    @property
    def trusted_proxies(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        nets = []
        for part in self.trusted_proxies_raw.split(","):
            if part.strip():
                nets.append(ipaddress.ip_network(part.strip(), strict=False))
        return nets

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/api/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.base_url.startswith("https://")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "downtube.db"

    def validate_runtime(self) -> list[str]:
        """Return human-readable configuration problems (empty list = OK)."""
        problems = []
        if len(self.secret_key) < 32:
            problems.append("DOWNTUBE_SECRET_KEY must be set and at least 32 characters")
        return problems


@lru_cache
def get_config() -> Config:
    return Config()
