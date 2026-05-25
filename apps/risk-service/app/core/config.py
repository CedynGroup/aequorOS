from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]

SETTINGS_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AppSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app_env: AppEnv = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="risk-service", alias="APP_NAME")


class DatabaseSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    database_url: str | None = Field(default=None, alias="DATABASE_URL")


class CorsSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.origins_raw.split(",") if origin.strip()]


class LoggingSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, log_level: str) -> str:
        return log_level.upper()


class Settings(BaseSettings):
    model_config = SETTINGS_CONFIG

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
