from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ACCUFLOW_",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    database_path: Path = Path("data/accuflow.db")
    initial_symbols: str = "AAPL,NVDA"

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = Field(default=4002, ge=1, le=65535)
    ibkr_client_id: int = Field(default=17, ge=1)
    ibkr_connect_timeout: float = Field(default=6.0, gt=0, le=60)
    ibkr_request_timeout: float = Field(default=60.0, gt=0, le=180)
    ibkr_market_data_type: int = Field(default=1, ge=1, le=4)
    ibkr_connect_on_startup: bool = False

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    @property
    def initial_symbol_list(self) -> list[str]:
        return [
            symbol.strip().upper()
            for symbol in self.initial_symbols.split(",")
            if symbol.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
