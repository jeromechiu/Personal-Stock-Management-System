"""Initialize the remote MariaDB portfolio with the requested Taiwan holdings."""

from __future__ import annotations

import argparse

import yfinance as yf
from dotenv import load_dotenv

from database import initialize_database, insert_holding_if_absent


def latest_close(symbol: str) -> float:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="5d")
    closes = history["Close"].dropna() if not history.empty else []
    if len(closes) == 0:
        raise RuntimeError(f"Unable to get the latest closing price for {symbol}")
    return round(float(closes.iloc[-1]), 6)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the MariaDB portfolio.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset all holdings, simulated orders, settlements, and simulated cash.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(".env")
    args = parse_args()
    initialize_database(reset=args.reset)

    for symbol in ("0050.TW", "0056.TW"):
        close_price = latest_close(symbol)
        inserted = insert_holding_if_absent(
            symbol=symbol,
            quantity=10_000,
            average_cost=close_price,
            currency="TWD",
        )
        if inserted:
            print(f"{symbol}: 10 張（10,000 股），初始成本 TWD {close_price}")
        else:
            print(
                f"{symbol}: already initialized; unchanged "
                "(use --reset to reinitialize)"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
