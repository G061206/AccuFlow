"""Validated boundaries for the deterministic M2 detector."""
from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    known_at: AwareDatetime
    effective_session: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    units_verified: bool = False
    adjustment_verified: bool = False
    event_review_through: AwareDatetime | None = None
    major_event: bool = False
    event_notes: str = Field(default="", max_length=2000)
    market_symbol: str | None = Field(default="SPY", max_length=12)
    sector_symbol: str | None = Field(default=None, max_length=12)
    bar_feed: Literal["historical_trades_rth"] = "historical_trades_rth"
    tick_size: float = Field(default=0.01, gt=0)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["trade", "quote"]
    event_time: AwareDatetime
    received_at: AwareDatetime
    sequence: int = Field(ge=0)
    connection_session: str = Field(min_length=1, max_length=100)
    source: Literal["IBKR"] = "IBKR"
    feed_type: Literal["live_tick_by_tick"] = "live_tick_by_tick"
    unit: Literal["shares", "unknown"] = "unknown"
    price: float | None = Field(default=None, gt=0)
    size: float | None = Field(default=None, ge=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    timestamp_precision_seconds: float = Field(default=1.0, gt=0, le=60)

    @model_validator(mode="after")
    def required_fields(self):
        if self.kind == "trade" and (self.price is None or self.size is None):
            raise ValueError("trade requires price and size")
        if self.kind == "quote" and (self.bid is None or self.ask is None):
            raise ValueError("quote requires bid and ask")
        return self


class StreamInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime
    connection_session: str
    trades_connected: bool
    quotes_connected: bool

    @model_validator(mode="after")
    def positive_interval(self):
        if self.end <= self.start:
            raise ValueError("stream interval end must follow start")
        return self
