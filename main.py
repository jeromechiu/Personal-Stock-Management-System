"""Local AI-powered stock and asset management agent."""

from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv

from agents import telegram_agent
from agents.crew import create_crew
from agents.decision_models import PortfolioDecision
from database import (
    MAX_DAILY_ORDERS,
    execute_simulated_order,
    get_simulation_cash,
    get_today_order_count,
    is_manager_paused,
    get_user_portfolio,
    initialize_database,
    is_taiwan_trading_window,
)
from market_data import fetch_equity_data, fetch_macro_context, macro_data_complete
from scheduler import TAIPEI, next_taiwan_analysis_time


BASE_DIR = Path(__file__).resolve().parent
ACTION_LABELS = {"ADD": "加碼", "REDUCE": "減碼", "HOLD": "持平"}


def fetch_stock_price(symbol: str) -> dict:
    return {
        **fetch_equity_data(symbol),
        "timestamp": datetime.now(TAIPEI).isoformat(timespec="seconds"),
    }


def build_market_snapshot(tickers: Iterable[str]) -> list[dict]:
    snapshot = []
    for raw_symbol in tickers:
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        try:
            snapshot.append(fetch_stock_price(symbol))
        except Exception as exc:
            snapshot.append({"symbol": symbol, "error": str(exc)})
    return snapshot


def build_portfolio_snapshot(
    markets: list[dict],
    holdings: list[dict] | None = None,
) -> list[dict]:
    prices = {
        item["symbol"]: item
        for item in markets
        if "price" in item
    }
    portfolio = []
    current_holdings = get_user_portfolio() if holdings is None else holdings
    for holding in current_holdings:
        market_data = prices.get(holding["symbol"], {})
        price = market_data.get("price")
        currency = str(holding.get("currency", "")).upper()
        quote_currency = str(market_data.get("currency", "")).upper()
        currency_matches = bool(currency) and currency == quote_currency
        quantity = float(holding["quantity"])
        average_cost = float(holding["average_cost"])
        market_value = (
            round(quantity * price, 4)
            if price is not None and currency_matches
            else None
        )
        cost_basis = round(quantity * average_cost, 4)
        unrealized_pnl = (
            round(market_value - cost_basis, 4)
            if market_value is not None
            else None
        )
        portfolio.append(
            {
                **holding,
                "current_price": price,
                "quote_currency": quote_currency or None,
                "currency_matches": currency_matches,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "unrealized_pnl": unrealized_pnl,
                "technical_timeframes": market_data.get("technical_timeframes"),
            }
        )
    return portfolio


def summarize_portfolio(portfolio: list[dict], available_cash: float) -> dict:
    market_values = [holding.get("market_value") for holding in portfolio]
    unrealized_values = [holding.get("unrealized_pnl") for holding in portfolio]
    cost_basis = sum(
        float(holding.get("cost_basis", 0) or 0) for holding in portfolio
    )
    market_value = (
        round(sum(float(value) for value in market_values), 2)
        if all(value is not None for value in market_values)
        else None
    )
    unrealized_pnl = (
        round(sum(float(value) for value in unrealized_values), 2)
        if all(value is not None for value in unrealized_values)
        else None
    )
    cash = round(float(available_cash), 2)
    return {
        "holding_count": len(portfolio),
        "holdings_market_value_twd": market_value,
        "available_cash_twd": cash,
        "total_assets_twd": (
            round(market_value + cash, 2) if market_value is not None else None
        ),
        "cost_basis_twd": round(cost_basis, 2),
        "unrealized_pnl_twd": unrealized_pnl,
        "valuation_complete": market_value is not None,
    }


def send_telegram(report: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required to send a report."
        )

    remaining = report
    while remaining:
        if len(remaining) <= 4000:
            part, remaining = remaining, ""
        else:
            newline = remaining.rfind("\n", 0, 4000)
            split_at = newline + 1 if newline > 0 else 4000
            part, remaining = remaining[:split_at], remaining[split_at:]

        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": part,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local AI portfolio analysis agent."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Ticker symbols to analyze (for example AAPL 0050.TW)",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Start Telegram commands and send completed reports",
    )
    parser.add_argument(
        "--analyze-only",
        "--no-settle",
        dest="analyze_only",
        action="store_true",
        help="Analyze and report without executing simulated orders",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one analysis immediately, only during Taiwan market hours",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("TRACK_INTERVAL_SECONDS", "900")),
        help="Tracking interval in seconds (default: 900)",
    )
    return parser.parse_args()


def start_telegram_agent() -> threading.Event:
    telegram_agent.validate_configuration()
    stop_event = threading.Event()
    telegram_thread = threading.Thread(
        target=telegram_agent.run_agent,
        kwargs={"stop_event": stop_event, "initialize_db": False},
        name="telegram-agent",
        daemon=True,
    )
    telegram_thread.start()
    return stop_event


def run_cycle(args: argparse.Namespace) -> int:
    if is_manager_paused():
        print("投資組合經理人目前暫停中，跳過本輪分析與交易。")
        return 0

    if not is_taiwan_trading_window():
        print("目前不在台股交易時段，跳過分析。")
        return 0

    holdings = get_user_portfolio()
    if args.tickers:
        ticker_values = [symbol.strip().upper() for symbol in args.tickers if symbol.strip()]
    else:
        ticker_values = [holding["symbol"] for holding in holdings]
        if not ticker_values:
            print("No active holdings found in MariaDB.")
            return 1

    markets = build_market_snapshot(ticker_values)
    portfolio = build_portfolio_snapshot(markets, holdings)
    trading_window_open = True
    tracked_holdings = {holding["symbol"] for holding in holdings}
    holding_currencies = {
        holding["symbol"]: str(holding.get("currency", "")).upper()
        for holding in holdings
    }
    markets_by_symbol = {market["symbol"]: market for market in markets}
    tracked_market_data = [
        markets_by_symbol.get(symbol, {"symbol": symbol, "error": "No market data"})
        for symbol in tracked_holdings
    ]
    if not all(
        market.get("price_is_fresh_today", False)
        for market in tracked_market_data
    ):
        print("尚無所有持股的今日台股報價（可能是假日或休市），跳過本輪分析。")
        return 0

    macro_context = fetch_macro_context()
    required_data_available = (
        macro_data_complete(macro_context)
        and all(
            "error" not in market
            and market.get("history", {}).get("returns", {}).get("3y") is not None
            and str(market.get("currency", "")).upper()
            == holding_currencies.get(market["symbol"])
            for market in tracked_market_data
        )
    )
    if trading_window_open:
        required_data_available = required_data_available and all(
            market.get("price_is_fresh_today", False)
            for market in tracked_market_data
        )

    available_cash = get_simulation_cash()
    portfolio_summary = summarize_portfolio(portfolio, available_cash)
    today_order_count = get_today_order_count()
    remaining_orders = max(0, MAX_DAILY_ORDERS - today_order_count)

    try:
        crew_result = create_crew(
            markets=markets,
            portfolio=portfolio,
            portfolio_summary=portfolio_summary,
            macro_context=macro_context,
            available_cash=available_cash,
            remaining_orders=remaining_orders,
            trading_window_open=trading_window_open,
            required_data_available=required_data_available,
        ).kickoff()
        decision = crew_result.pydantic
        if not isinstance(decision, PortfolioDecision):
            raise RuntimeError("Portfolio Manager returned no validated decision plan")
    except Exception as exc:
        print(f"Analysis failed: {exc}")
        return 1

    report_lines = [
        "投資組合模擬分析報告",
        f"時間：{datetime.now(TAIPEI).isoformat(timespec='seconds')}",
        "",
        "資產概況（程式依持倉市值與模擬現金計算）",
        f"持倉總市值：{_format_twd(portfolio_summary['holdings_market_value_twd'])}",
        f"模擬可用現金：{_format_twd(portfolio_summary['available_cash_twd'])}",
        f"總資產（持倉總市值 + 現金）：{_format_twd(portfolio_summary['total_assets_twd'])}",
        f"持倉成本：{_format_twd(portfolio_summary['cost_basis_twd'])}",
        f"未實現損益：{_format_twd(portfolio_summary['unrealized_pnl_twd'])}",
        "",
        "宏觀分析",
        decision.macro_assessment,
        "",
        "個股決策",
    ]
    order_results = []
    available_cash_after = available_cash
    orders_left = remaining_orders
    market_prices = {
        item["symbol"]: item["price"] for item in markets if "price" in item
    }

    for item in decision.decisions:
        symbol = item.symbol.upper()
        action = item.action
        report_lines.extend(
            [
                f"{symbol}：市場訊號「{ACTION_LABELS[item.market_signal]}」；"
                f"經理人「{ACTION_LABELS[action]}」"
                f"（{'採納' if action == item.market_signal else '未完全採納'}）",
                f"建議股數：{item.lots * 1000:,} 股（{item.lots} 張）",
                f"決策原因：{item.rationale}",
            ]
        )

        if action == "HOLD":
            continue
        if args.analyze_only:
            order_results.append(f"{symbol}：分析模式，未模擬成交。")
            continue
        if not trading_window_open:
            order_results.append(f"{symbol}：非台灣交易時段，未模擬成交。")
            continue
        if not required_data_available:
            order_results.append(f"{symbol}：必需市場資料不完整或過期，未模擬成交。")
            continue
        if symbol not in tracked_holdings:
            order_results.append(f"{symbol}：非目前持倉標的，未模擬成交。")
            continue
        if orders_left <= 0:
            order_results.append("今日模擬委託已達 3 筆上限，後續決策未成交。")
            continue
        if item.lots < 1:
            order_results.append(f"{symbol}：加減碼張數必須大於 0，未模擬成交。")
            continue

        try:
            result = execute_simulated_order(
                symbol=symbol,
                market_signal=item.market_signal,
                action=action,
                lots=item.lots,
                price=market_prices[symbol],
                rationale=item.rationale,
            )
        except Exception as exc:
            order_results.append(f"{symbol}：模擬委託失敗：{exc}")
            continue

        orders_left -= 1
        available_cash_after = result["cash_after"]
        order_results.append(
            f"{symbol}：模擬{ACTION_LABELS[action]} {result['shares']:,} 股，"
            f"成交價 TWD {result['price']:,.2f}；"
            f"已實現損益（未扣費稅）TWD "
            f"{result['gross_realized_pnl']:,.2f}；"
            f"剩餘持股 {result['quantity_after']:,.0f} 股；"
            f"模擬現金 TWD {available_cash_after:,.2f}。"
        )

    report_lines.extend(["", "模擬委託結果"])
    report_lines.extend(order_results or ["本輪沒有加碼或減碼委託；持平不寫入委託或結算紀錄。"])
    report_lines.extend(
        [
            f"今日剩餘模擬委託：{orders_left} 筆（每日最多 {MAX_DAILY_ORDERS} 筆）",
            f"模擬可用現金：TWD {available_cash_after:,.2f}",
            "",
            "風險與免責聲明",
            decision.risk_summary,
            "本系統只在本機模擬成交，不會連接券商或發出真實委託；"
            "損益為估算值，未扣手續費、交易稅、滑價或流動性影響。"
            "歷史績效不代表未來表現。",
        ]
    )
    report = "\n".join(report_lines)
    print(report)
    if args.telegram:
        try:
            send_telegram(report)
            print("已傳送 Telegram 報告。")
        except Exception as exc:
            print(f"Telegram 傳送失敗：{exc}")
            return 1

    return 0


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    args = parse_args()
    initialize_database()

    if args.interval < 60:
        print("Tracking interval must be at least 60 seconds.")
        return 1

    if args.once:
        run_cycle(args)
        return 0

    telegram_stop_event = None
    if args.telegram:
        telegram_stop_event = start_telegram_agent()
        print("Telegram 指令服務已啟動。")

    try:
        next_run = next_taiwan_analysis_time(
            datetime.now(TAIPEI),
            args.interval,
            include_current=True,
        )
        while True:
            wait_seconds = max(
                0.0, (next_run - datetime.now(TAIPEI)).total_seconds()
            )
            print(
                f"下次分析時間：{next_run.isoformat(timespec='minutes')} "
                f"（台北時間），等待 {int(wait_seconds)} 秒。"
            )
            time.sleep(wait_seconds)
            print(f"\n[{datetime.now(TAIPEI).isoformat(timespec='seconds')}] 開始分析")
            run_cycle(args)
            next_run = next_taiwan_analysis_time(
                datetime.now(TAIPEI),
                args.interval,
                include_current=False,
            )
    except KeyboardInterrupt:
        print("\n已停止排程。")
    finally:
        if telegram_stop_event is not None:
            telegram_stop_event.set()

    return 0


def _format_twd(amount: float | None) -> str:
    if amount is None:
        return "無法計算（報價資料不完整）"
    return f"TWD {amount:,.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
