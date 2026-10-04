from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # ==================== App Settings ====================
    APP_NAME: str = "crm_leads_api"
    DEBUG: bool = False
    TESTING: bool = False
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = []
    LOG_DIR: str = "logs"

    # ==================== Database Settings ====================
    DATABASE_URL: str = Field(..., description="Async SQLAlchemy URL (postgresql+asyncpg://...)")

    # ==================== JWT Settings ====================
    JWT_SECRET_KEY: str = Field(..., description="Secret key for signing tokens")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ==================== Lead Settings ====================
    DEFAULT_COUNTRY_CODE: str = "91"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_secret_keys(cls, v: str, info) -> str:
        if not v or len(v) < 32:
            raise ValueError(f"{info.field_name} must be at least 32 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
