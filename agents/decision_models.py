"""Structured agent outputs used by the paper-trading workflow."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


Action = Literal["ADD", "REDUCE", "HOLD"]


class MarketSignal(BaseModel):
    symbol: str
    signal: Action
    rationale: str


class MarketAnalysis(BaseModel):
    signals: list[MarketSignal]
    macro_assessment: str


class AssetDecision(BaseModel):
    symbol: str
    market_signal: Action
    action: Action
    lots: int = Field(ge=0, description="Number of 1,000-share lots; zero for HOLD.")
    rationale: str

    @model_validator(mode="after")
    def validate_action_size(self) -> "AssetDecision":
        if self.action == "HOLD" and self.lots != 0:
            raise ValueError("HOLD decisions must have zero lots")
        if self.action != "HOLD" and self.lots < 1:
            raise ValueError("ADD and REDUCE decisions must have at least one lot")
        return self


class PortfolioDecision(BaseModel):
    overview: str
    macro_assessment: str
    decisions: list[AssetDecision]
    risk_summary: str

    @model_validator(mode="after")
    def validate_unique_symbols(self) -> "PortfolioDecision":
        symbols = [decision.symbol.upper() for decision in self.decisions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Portfolio decisions must contain each symbol only once")
        return self
