from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import Literal
from pydantic import Field, SecretStr, model_validator
import re
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

    workflow_poll_seconds: float = Field(default=30, ge=5, le=300)
    workflow_stock_timeout: float = Field(default=900, ge=1, le=1800)
    workflow_job_timeout: float = Field(default=3600, ge=1, le=7200)

    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_sender: str = ""
    smtp_recipients: str = ""
    smtp_timeout: float = Field(default=10, gt=0, le=60)
    delivery_poll_seconds: float = Field(default=10, ge=1, le=300)
    delivery_max_attempts: int = Field(default=5, ge=1, le=10)
    metrics_interval_seconds: float = Field(default=30, ge=1, le=300)
    metrics_retention_days: int = Field(default=14, ge=1, le=90)

    @property
    def recipient_list(self):
        return sorted(set(x.strip() for x in self.smtp_recipients.split(",") if x.strip()))

    @model_validator(mode="after")
    def validate_delivery(self):
        if self.smtp_enabled:
            if not self.smtp_host or not self.smtp_sender or not self.recipient_list:
                raise ValueError("SMTP enabled requires host, sender and recipients")
            addresses=[self.smtp_sender]+self.recipient_list
            if len(self.recipient_list)>10 or any(not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+",x) for x in addresses):
                raise ValueError("SMTP requires up to ten plain ASCII recipient addresses")
        return self

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
