from __future__ import annotations

import argparse
import contextlib
import io
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

import database
import main
from agents import llm_config
from agents import telegram_agent
from agents.decision_models import AssetDecision, PortfolioDecision
from market_data import (
    macro_data_complete,
    summarize_close_prices,
    summarize_technical_timeframes,
)
from scheduler import TAIPEI, next_taiwan_analysis_time


class SimulationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.holdings = [
            {
                "symbol": "0050.TW",
                "quantity": 10_000.0,
                "average_cost": 100.0,
                "currency": "TWD",
            }
        ]
        self.markets = [
            {
                "symbol": "0050.TW",
                "price": 105.0,
                "currency": "TWD",
                "price_is_fresh_today": True,
                "history": {"returns": {"3y": 0.2}},
            }
        ]
        self.macro = {
            "oil": {},
            "semiconductor_index": {},
            "fed_policy_rate": {},
        }

    def run_with_decision(self, decision: PortfolioDecision):
        crew = Mock()
        crew.kickoff.return_value = SimpleNamespace(pydantic=decision)
        with (
            patch.object(main, "get_user_portfolio", return_value=self.holdings),
            patch.object(main, "build_market_snapshot", return_value=self.markets),
            patch.object(main, "build_portfolio_snapshot", return_value=self.holdings),
            patch.object(main, "fetch_macro_context", return_value=self.macro),
            patch.object(main, "macro_data_complete", return_value=True),
            patch.object(main, "is_taiwan_trading_window", return_value=True),
            patch.object(main, "get_simulation_cash", return_value=150_000),
            patch.object(main, "get_today_order_count", return_value=0),
            patch.object(main, "is_manager_paused", return_value=False),
            patch.object(main, "create_crew", return_value=crew),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main.run_cycle(
                argparse.Namespace(
                    tickers=None,
                    analyze_only=False,
                    telegram=False,
                )
            )
        return result

    def test_hold_does_not_create_an_order_or_settlement(self) -> None:
        decision = PortfolioDecision(
            overview="持平",
            macro_assessment="資料正常",
            decisions=[
                AssetDecision(
                    symbol="0050.TW",
                    market_signal="HOLD",
                    action="HOLD",
                    lots=0,
                    rationale="風險與報酬不對稱",
                )
            ],
            risk_summary="不保證獲利",
        )
        with patch.object(main, "execute_simulated_order") as execute:
            self.assertEqual(self.run_with_decision(decision), 0)
        execute.assert_not_called()

    def test_add_decision_uses_manager_lots(self) -> None:
        decision = PortfolioDecision(
            overview="加碼",
            macro_assessment="資料正常",
            decisions=[
                AssetDecision(
                    symbol="0050.TW",
                    market_signal="ADD",
                    action="ADD",
                    lots=1,
                    rationale="趨勢與風險符合模擬策略",
                )
            ],
            risk_summary="不保證獲利",
        )
        expected = {
            "shares": 1000,
            "price": 105.0,
            "gross_realized_pnl": 0.0,
            "quantity_after": 11_000,
            "cash_after": 45_000,
        }
        with patch.object(
            main, "execute_simulated_order", return_value=expected
        ) as execute:
            self.assertEqual(self.run_with_decision(decision), 0)
        self.assertEqual(execute.call_args.kwargs["lots"], 1)

    def test_taiwan_trading_window_and_three_year_metrics(self) -> None:
        timezone = ZoneInfo("Asia/Taipei")
        self.assertTrue(
            database.is_taiwan_trading_window(
                datetime(2026, 11, 23, 9, 0, tzinfo=timezone)
            )
        )
        self.assertFalse(
            database.is_taiwan_trading_window(
                datetime(2026, 11, 22, 10, 0, tzinfo=timezone)
            )
        )
        dates = pd.date_range("2023-01-01", periods=1100, freq="D", tz=timezone)
        summary = summarize_close_prices(
            pd.Series(range(100, 1200), index=dates, dtype=float)
        )
        self.assertIsNotNone(summary["returns"]["3y"])
        self.assertGreater(summary["observations_3y"], 700)

    def test_assets_add_cash_once_and_include_multi_timeframe_kd(self) -> None:
        portfolio = [
            {"market_value": 660_000, "cost_basis": 600_000, "unrealized_pnl": 60_000},
            {"market_value": 627_600, "cost_basis": 620_000, "unrealized_pnl": 7_600},
        ]
        summary = main.summarize_portfolio(portfolio, available_cash=150_000)
        self.assertEqual(summary["holdings_market_value_twd"], 1_287_600)
        self.assertEqual(summary["total_assets_twd"], 1_437_600)

        dates = pd.date_range("2008-01-01", periods=6500, freq="D", tz="Asia/Taipei")
        closes = pd.Series(range(100, 6600), index=dates, dtype=float)
        history = pd.DataFrame(
            {
                "Open": closes - 0.5,
                "High": closes + 1,
                "Low": closes - 1,
                "Close": closes,
            },
            index=dates,
        )
        timeframes = summarize_technical_timeframes(history)
        self.assertEqual(set(timeframes), {"daily", "weekly", "monthly", "yearly"})
        for timeframe in timeframes.values():
            self.assertIsNotNone(timeframe["kd"]["k"])
            self.assertIsNotNone(timeframe["kd"]["d"])

    def test_incomplete_macro_history_disables_trading(self) -> None:
        macro = {
            "oil": {"history": {"returns": {"3y": 0.1}}},
            "semiconductor_index": {"history": {"returns": {"3y": None}}},
            "fed_policy_rate": {"target_range_changes_3y": [{"date": "2024-01-01"}]},
        }
        self.assertFalse(macro_data_complete(macro))

    def test_schedule_aligns_to_open_and_skips_closed_days(self) -> None:
        self.assertEqual(
            next_taiwan_analysis_time(datetime(2026, 11, 23, 8, 0, tzinfo=TAIPEI)),
            datetime(2026, 11, 23, 9, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(
            next_taiwan_analysis_time(datetime(2026, 11, 23, 9, 7, tzinfo=TAIPEI)),
            datetime(2026, 11, 23, 9, 15, tzinfo=TAIPEI),
        )
        self.assertEqual(
            next_taiwan_analysis_time(
                datetime(2026, 11, 23, 9, 0, tzinfo=TAIPEI),
                include_current=False,
            ),
            datetime(2026, 11, 23, 9, 15, tzinfo=TAIPEI),
        )
        self.assertEqual(
            next_taiwan_analysis_time(datetime(2026, 11, 22, 10, 0, tzinfo=TAIPEI)),
            datetime(2026, 11, 23, 9, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(
            next_taiwan_analysis_time(
                datetime(2026, 11, 23, 13, 30, tzinfo=TAIPEI),
                include_current=False,
            ),
            datetime(2026, 11, 24, 9, 0, tzinfo=TAIPEI),
        )

    def test_run_cycle_skips_before_database_work_outside_market_hours(self) -> None:
        with (
            patch.object(main, "is_manager_paused", return_value=False),
            patch.object(main, "is_taiwan_trading_window", return_value=False),
            patch.object(main, "get_user_portfolio") as get_portfolio,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main.run_cycle(argparse.Namespace())
        self.assertEqual(result, 0)
        get_portfolio.assert_not_called()

    def test_run_cycle_skips_while_manager_is_paused(self) -> None:
        with (
            patch.object(main, "is_manager_paused", return_value=True),
            patch.object(main, "is_taiwan_trading_window") as trading_window,
            patch.object(main, "get_user_portfolio") as get_portfolio,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main.run_cycle(argparse.Namespace())
        self.assertEqual(result, 0)
        trading_window.assert_not_called()
        get_portfolio.assert_not_called()

    def test_telegram_pause_duration_supports_units_and_limits(self) -> None:
        self.assertEqual(telegram_agent._parse_pause_minutes("30"), 30)
        self.assertEqual(telegram_agent._parse_pause_minutes("2h"), 120)
        self.assertEqual(telegram_agent._parse_pause_minutes("1d"), 1440)
        with self.assertRaises(ValueError):
            telegram_agent._parse_pause_minutes("8d")

    def test_natural_language_asset_query_uses_portfolio_data(self) -> None:
        intent = telegram_agent.TelegramIntent(
            intent="assets",
            response="好的，這是目前的資產狀況。",
        )
        with (
            patch.object(telegram_agent, "interpret_message", return_value=intent),
            patch.object(
                telegram_agent,
                "build_asset_report",
                return_value=(
                    [{"symbol": "0050.TW", "quantity": 1000, "market_value": 105000}],
                    {
                        "holdings_market_value_twd": 105000,
                        "available_cash_twd": 10000,
                        "total_assets_twd": 115000,
                        "cost_basis_twd": 100000,
                        "unrealized_pnl_twd": 5000,
                    },
                ),
            ),
            patch.object(telegram_agent, "send_message") as send,
        ):
            answer = telegram_agent.handle_message(
                "token",
                123,
                "可以告訴我目前資產嗎？",
                [],
                Mock(),
            )
        self.assertIn("目前的資產狀況", answer)
        self.assertIn("TWD 115,000.00", answer)
        send.assert_called_once()

    def test_natural_language_today_orders_reports_sold_shares(self) -> None:
        intent = telegram_agent.TelegramIntent(
            intent="orders",
            response="我查一下今天的模擬交易。",
        )
        with (
            patch.object(telegram_agent, "interpret_message", return_value=intent),
            patch.object(
                telegram_agent,
                "get_today_simulated_orders",
                return_value=[
                    {
                        "symbol": "0050.TW",
                        "action": "REDUCE",
                        "lots": 1,
                        "shares": 1000,
                        "price": 105.0,
                        "gross_realized_pnl": 5000.0,
                    }
                ],
            ),
            patch.object(telegram_agent, "send_message") as send,
        ):
            answer = telegram_agent.handle_message(
                "token",
                123,
                "今天賣出幾股？",
                [],
                Mock(),
            )
        self.assertIn("賣出：1,000 股", answer)
        self.assertIn("0050.TW", answer)
        send.assert_called_once()

    def test_natural_language_pause_rejects_duration_over_seven_days(self) -> None:
        intent = telegram_agent.TelegramIntent(
            intent="pause",
            response="我會暫停經理人。",
            pause_minutes=10_081,
        )
        with (
            patch.object(telegram_agent, "interpret_message", return_value=intent),
            patch.object(telegram_agent, "pause_manager_for") as pause,
            patch.object(telegram_agent, "send_message") as send,
        ):
            answer = telegram_agent.handle_message(
                "token",
                123,
                "暫停交易",
                [],
                Mock(),
            )
        pause.assert_not_called()
        self.assertIn("7 天", answer)
        send.assert_called_once()

    def test_telegram_asset_report_discloses_incomplete_valuation(self) -> None:
        report = telegram_agent.format_asset_report(
            [
                {
                    "symbol": "0050.TW",
                    "quantity": 1000,
                    "current_price": None,
                    "market_value": None,
                }
            ],
            {
                "holdings_market_value_twd": None,
                "available_cash_twd": 1000,
                "total_assets_twd": None,
                "cost_basis_twd": 1000,
                "unrealized_pnl_twd": None,
            },
        )
        self.assertIn("無法計算（報價資料不完整）", report)
        self.assertIn("報價不完整", report)

    def test_main_starts_telegram_agent_in_background(self) -> None:
        with (
            patch.object(telegram_agent, "validate_configuration") as validate,
            patch.object(main.threading, "Thread") as thread_class,
        ):
            stop_event = main.start_telegram_agent()
        validate.assert_called_once_with()
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once_with()
        thread_class.assert_called_once_with(
            target=telegram_agent.run_agent,
            kwargs={"stop_event": stop_event, "initialize_db": False},
            name="telegram-agent",
            daemon=True,
        )

    def test_llm_model_is_required_and_read_from_environment(self) -> None:
        with (
            patch.dict("os.environ", {"GEMINI_MODEL": "", "GEMINI_API_KEY": "key"}),
            self.assertRaises(RuntimeError),
        ):
            llm_config.create_llm()

        with (
            patch.dict(
                "os.environ",
                {"GEMINI_MODEL": "provider/custom-model", "GEMINI_API_KEY": "key"},
            ),
            patch.object(llm_config, "LLM") as llm_constructor,
        ):
            llm_config.create_llm()
        llm_constructor.assert_called_once_with(
            model="provider/custom-model",
            api_key="key",
        )


if __name__ == "__main__":
    unittest.main()
