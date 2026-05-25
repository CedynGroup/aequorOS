from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="risk-service", alias="APP_NAME")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    cors_origins_raw: str = Field(default="", alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
