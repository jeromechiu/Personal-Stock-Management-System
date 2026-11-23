"""Historical equity and macro-market data collection."""

from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yfinance as yf


TAIPEI = ZoneInfo("Asia/Taipei")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
HISTORY_YEARS = 15
TIMEFRAME_RULES = {
    "daily": None,
    "weekly": "W-FRI",
    "monthly": "ME",
    "yearly": "YE",
}


def _date_string(value: Any) -> str:
    timestamp = value
    if getattr(timestamp, "tzinfo", None) is not None:
        timestamp = timestamp.tz_convert(TAIPEI)
    return timestamp.date().isoformat()


def summarize_close_prices(close: Any, as_of: date | None = None) -> dict:
    close = close.dropna().sort_index()
    if len(close) < 2:
        raise ValueError("At least two daily close prices are required")

    latest = float(close.iloc[-1])
    latest_date = _date_string(close.index[-1])
    as_of = as_of or datetime.now(TAIPEI).date()
    periods = {"3y": 1095, "1y": 365, "6m": 182, "3m": 91, "1m": 30, "5d": 5}
    returns = {}
    for label, days in periods.items():
        target = as_of - timedelta(days=days)
        eligible = close[[index.date() <= target for index in close.index]]
        returns[label] = (
            round(latest / float(eligible.iloc[-1]) - 1, 4)
            if not eligible.empty
            else None
        )

    three_year_close = close[
        [index.date() >= as_of - timedelta(days=3 * 365 + 10) for index in close.index]
    ]
    year_ago = close[
        [index.date() >= as_of - timedelta(days=365) for index in close.index]
    ]
    daily_returns = three_year_close.pct_change().dropna()
    drawdown = three_year_close / three_year_close.cummax() - 1

    return {
        "latest_close": round(latest, 4),
        "latest_bar_date": latest_date,
        "observations_3y": int(len(three_year_close)),
        "returns": returns,
        "annualized_volatility": (
            round(float(daily_returns.std() * math.sqrt(252)), 4)
            if not daily_returns.empty
            else None
        ),
        "max_drawdown_3y": round(float(drawdown.min()), 4),
        "high_52w": round(float(year_ago.max()), 4) if not year_ago.empty else None,
        "low_52w": round(float(year_ago.min()), 4) if not year_ago.empty else None,
    }


def _kd_values(candles: Any, lookback: int = 9) -> dict:
    lowest = candles["Low"].rolling(lookback, min_periods=lookback).min()
    highest = candles["High"].rolling(lookback, min_periods=lookback).max()
    spread = highest - lowest
    rsv = ((candles["Close"] - lowest) / spread * 100).where(spread != 0, 50).dropna()
    if rsv.empty:
        return {"k": None, "d": None, "cross": "資料不足", "zone": "資料不足"}

    k_values = []
    d_values = []
    previous_k = previous_d = 50.0
    for value in rsv:
        current_k = (2 * previous_k + float(value)) / 3
        current_d = (2 * previous_d + current_k) / 3
        k_values.append(current_k)
        d_values.append(current_d)
        previous_k, previous_d = current_k, current_d

    cross = "無交叉"
    if len(k_values) > 1:
        if k_values[-2] <= d_values[-2] and k_values[-1] > d_values[-1]:
            cross = "黃金交叉"
        elif k_values[-2] >= d_values[-2] and k_values[-1] < d_values[-1]:
            cross = "死亡交叉"

    latest_k, latest_d = k_values[-1], d_values[-1]
    zone = "超買區" if latest_k >= 80 and latest_d >= 80 else (
        "超賣區" if latest_k <= 20 and latest_d <= 20 else "中性區"
    )
    return {
        "k": round(latest_k, 2),
        "d": round(latest_d, 2),
        "cross": cross,
        "zone": zone,
        "lookback_periods": lookback,
    }


def _timeframe_summary(candles: Any) -> dict:
    candles = candles.dropna(subset=["High", "Low", "Close"]).sort_index()
    if candles.empty:
        return {"error": "No candles available"}

    latest = candles.iloc[-1]
    previous = candles.iloc[-2] if len(candles) > 1 else None
    averages = {
        str(window): round(float(candles["Close"].tail(window).mean()), 4)
        if len(candles) >= window
        else None
        for window in (5, 20, 60)
    }
    sma20 = averages["20"]

    return {
        "latest_period": _date_string(candles.index[-1]),
        "open": round(float(latest["Open"]), 4) if "Open" in candles else None,
        "high": round(float(latest["High"]), 4),
        "low": round(float(latest["Low"]), 4),
        "close": round(float(latest["Close"]), 4),
        "change_from_previous_period": (
            round(float(latest["Close"]) / float(previous["Close"]) - 1, 4)
            if previous is not None and float(previous["Close"]) != 0
            else None
        ),
        "moving_averages": averages,
        "close_vs_sma20": (
            "above" if float(latest["Close"]) >= sma20 else "below"
        )
        if sma20 is not None
        else "資料不足",
        "kd": _kd_values(candles),
    }


def summarize_technical_timeframes(history: Any) -> dict:
    ohlc = history.dropna(subset=["High", "Low", "Close"]).sort_index()
    summaries = {}
    for timeframe, rule in TIMEFRAME_RULES.items():
        if rule is None:
            candles = ohlc
        else:
            candles = ohlc.resample(rule).agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                }
            )
        summaries[timeframe] = _timeframe_summary(candles)
    return summaries


def fetch_equity_data(symbol: str) -> dict:
    today = datetime.now(TAIPEI).date()
    start = today - timedelta(days=HISTORY_YEARS * 365 + 10)
    ticker = yf.Ticker(symbol)
    history = ticker.history(
        start=start.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=True,
    )
    if history.empty or not {"Open", "High", "Low", "Close"}.issubset(history.columns):
        raise RuntimeError(f"No historical OHLC data returned for {symbol}")

    history_summary = summarize_close_prices(history["Close"], today)
    technical_timeframes = summarize_technical_timeframes(history)
    fast = ticker.fast_info
    current_price = getattr(fast, "last_price", None)
    if current_price is None or not math.isfinite(float(current_price)):
        current_price = history_summary["latest_close"]
    if float(current_price) <= 0:
        raise RuntimeError(f"Invalid current price returned for {symbol}")

    return {
        "symbol": symbol.upper(),
        "price": round(float(current_price), 4),
        "currency": getattr(fast, "currency", "UNKNOWN"),
        "history": history_summary,
        "technical_timeframes": technical_timeframes,
        "price_is_fresh_today": history_summary["latest_bar_date"] == today.isoformat(),
    }


def fetch_fed_target_history() -> dict:
    today = datetime.now(TAIPEI).date()
    start = today - timedelta(days=3 * 365 + 10)
    response = requests.get(
        FRED_URL,
        params={
            "id": "DFEDTARU,DFEDTARL",
            "cosd": start.isoformat(),
            "coed": today.isoformat(),
        },
        timeout=30,
    )
    response.raise_for_status()

    events = []
    previous_range = None
    latest_range = None
    for row in csv.DictReader(io.StringIO(response.text)):
        upper = row.get("DFEDTARU", "").strip()
        lower = row.get("DFEDTARL", "").strip()
        if not upper or not lower or upper == "." or lower == ".":
            continue
        current_range = (float(lower), float(upper))
        latest_range = current_range
        if current_range != previous_range:
            events.append(
                {
                    "date": row.get("observation_date", ""),
                    "lower_percent": current_range[0],
                    "upper_percent": current_range[1],
                }
            )
            previous_range = current_range

    if not events or latest_range is None:
        raise RuntimeError("FRED returned no Federal Funds target range observations")

    return {
        "source": "FRED DFEDTARL/DFEDTARU",
        "current_target_range_percent": {
            "lower": latest_range[0],
            "upper": latest_range[1],
        },
        "target_range_changes_3y": events,
    }


def fetch_macro_context() -> dict:
    indicators = {}
    for name, symbol in (("oil", "CL=F"), ("semiconductor_index", "^SOX")):
        try:
            indicator = fetch_equity_data(symbol)
            indicators[name] = {
                **indicator,
                "source": f"Yahoo Finance {symbol}",
            }
        except Exception as exc:
            indicators[name] = {"symbol": symbol, "error": str(exc)}

    try:
        indicators["fed_policy_rate"] = fetch_fed_target_history()
    except Exception as exc:
        indicators["fed_policy_rate"] = {"error": str(exc)}

    indicators["retrieved_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
    return indicators


def macro_data_complete(macro: dict) -> bool:
    for key in ("oil", "semiconductor_index"):
        indicator = macro.get(key)
        if (
            not isinstance(indicator, dict)
            or "error" in indicator
            or indicator.get("history", {}).get("returns", {}).get("3y") is None
        ):
            return False

    fed_rates = macro.get("fed_policy_rate")
    return (
        isinstance(fed_rates, dict)
        and "error" not in fed_rates
        and bool(fed_rates.get("target_range_changes_3y"))
    )
