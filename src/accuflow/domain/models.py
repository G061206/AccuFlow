from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol or len(symbol) > 12:
        raise ValueError("股票代码长度必须在 1 到 12 个字符之间")
    if not all(character.isalnum() or character in ".-" for character in symbol):
        raise ValueError("股票代码只能包含字母、数字、点或连字符")
    return symbol


class StockCreate(BaseModel):
    symbol: str

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


class StockActiveUpdate(BaseModel):
    active: bool


class ReportCreate(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    report_time: datetime
    report_type: Literal["收盘报告", "小时报告"]
    symbols: list[str] = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    judgment: str = Field(min_length=1, max_length=10000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    counter_evidence: list[str] = Field(default_factory=list, max_length=100)
    data_quality: str = Field(min_length=1, max_length=20000)
    rule_version: str = Field(default="unified-v1", min_length=1, max_length=50)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str]) -> list[str]:
        return [normalize_symbol(value) for value in values]


class ReportGenerateRequest(BaseModel):
    report_type: Literal["收盘报告", "小时报告"] = "收盘报告"


class BackfillRequest(BaseModel):
    include_daily: bool = True
    include_minute: bool = True


    @model_validator(mode="after")
    def require_interval(self):
        if not self.include_daily and not self.include_minute:
            raise ValueError("至少选择一种补数周期")
        return self


class Preferences(BaseModel):
    monitoring_enabled: bool = False
    daily_report: bool = True
    hourly_alert: bool = False


class PreferencesUpdate(BaseModel):
    monitoring_enabled: bool | None = None
    daily_report: bool | None = None
    hourly_alert: bool | None = None
