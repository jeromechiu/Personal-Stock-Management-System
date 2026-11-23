"""MariaDB connection and portfolio persistence."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor

from scheduler import MARKET_CLOSE, MARKET_OPEN, TAIPEI

SHARES_PER_LOT = 1000
MAX_DAILY_ORDERS = 3
MAX_MANAGER_PAUSE_MINUTES = 10_080


def mariadb_config(with_database: bool = True) -> dict:
    config = {
        "host": os.getenv("MARIADB_HOST", "127.0.0.1"),
        "port": int(os.getenv("MARIADB_PORT", "3306")),
        "user": os.getenv("MARIADB_USER", "root"),
        "password": os.getenv("MARIADB_PASSWORD", ""),
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if with_database:
        config["database"] = os.getenv("MARIADB_DATABASE", "portfolio")
    return config


def initialize_database(reset: bool = False) -> None:
    database = os.getenv("MARIADB_DATABASE", "portfolio")
    quoted_database = database.replace("`", "``")
    with pymysql.connect(**mariadb_config(with_database=False)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{quoted_database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )

    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS holdings (
                    symbol VARCHAR(32) PRIMARY KEY,
                    quantity DECIMAL(18,6) NOT NULL,
                    average_cost DECIMAL(18,6) NOT NULL,
                    currency VARCHAR(16) NOT NULL DEFAULT 'TWD',
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settlements (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    settlement_date DATE NOT NULL,
                    symbol VARCHAR(32) NOT NULL,
                    quantity DECIMAL(18,6) NOT NULL,
                    price DECIMAL(18,6) NOT NULL,
                    market_value DECIMAL(18,6) NOT NULL,
                    cost_basis DECIMAL(18,6) NOT NULL,
                    unrealized_pnl DECIMAL(18,6) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_settlement_symbol_date (symbol, settlement_date)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_account (
                    id TINYINT UNSIGNED PRIMARY KEY,
                    cash_balance DECIMAL(18,2) NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS simulated_orders (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    order_date DATE NOT NULL,
                    symbol VARCHAR(32) NOT NULL,
                    market_signal VARCHAR(8) NOT NULL,
                    action VARCHAR(8) NOT NULL,
                    lots INT UNSIGNED NOT NULL,
                    shares INT UNSIGNED NOT NULL,
                    price DECIMAL(18,6) NOT NULL,
                    gross_realized_pnl DECIMAL(18,2) NOT NULL,
                    unrealized_pnl_after DECIMAL(18,2) NOT NULL,
                    cash_after DECIMAL(18,2) NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    INDEX idx_simulated_orders_date (order_date)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_control (
                    id TINYINT UNSIGNED PRIMARY KEY,
                    paused_until DATETIME NULL
                )
                """
            )
            cursor.execute(
                "INSERT IGNORE INTO manager_control (id, paused_until) VALUES (1, NULL)"
            )
            initial_cash = Decimal(
                os.getenv("SIMULATION_INITIAL_CASH_TWD", "150000")
            )
            cursor.execute(
                """
                INSERT IGNORE INTO simulation_account (id, cash_balance)
                VALUES (1, %s)
                """,
                (initial_cash,),
            )
            if reset:
                cursor.execute("DELETE FROM simulated_orders")
                cursor.execute("DELETE FROM settlements")
                cursor.execute("DELETE FROM holdings")
                cursor.execute(
                    "UPDATE simulation_account SET cash_balance = %s WHERE id = 1",
                    (initial_cash,),
                )


def get_user_portfolio() -> list[dict]:
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor(DictCursor) as cursor:
            cursor.execute(
                """
                SELECT symbol, quantity, average_cost, currency
                FROM holdings
                WHERE quantity > 0
                ORDER BY symbol
                """
            )
            return [
                {
                    **row,
                    "quantity": float(row["quantity"]),
                    "average_cost": float(row["average_cost"]),
                }
                for row in cursor.fetchall()
            ]


def insert_holding_if_absent(
    symbol: str,
    quantity: float,
    average_cost: float,
    currency: str = "TWD",
) -> bool:
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT IGNORE INTO holdings (symbol, quantity, average_cost, currency)
                VALUES (%s, %s, %s, %s)
                """,
                (symbol.upper(), quantity, average_cost, currency),
            )
            return cursor.rowcount == 1


def get_simulation_cash() -> float:
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT cash_balance FROM simulation_account WHERE id = 1"
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Simulation account is not initialized")
            return float(row[0])


def get_today_order_count(now: datetime | None = None) -> int:
    trading_date = _taipei_moment(now).date()
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM simulated_orders WHERE order_date = %s",
                (trading_date,),
            )
            return int(cursor.fetchone()[0])


def get_today_simulated_orders(now: datetime | None = None) -> list[dict]:
    trading_date = _taipei_moment(now).date()
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor(DictCursor) as cursor:
            cursor.execute(
                """
                SELECT symbol, action, lots, shares, price,
                       gross_realized_pnl, created_at
                FROM simulated_orders
                WHERE order_date = %s
                ORDER BY created_at, id
                """,
                (trading_date,),
            )
            rows = cursor.fetchall()
            return [
                {
                    **row,
                    "lots": int(row["lots"]),
                    "shares": int(row["shares"]),
                    "price": float(row["price"]),
                    "gross_realized_pnl": float(row["gross_realized_pnl"]),
                }
                for row in rows
            ]


def get_manager_pause_until() -> datetime | None:
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT paused_until FROM manager_control WHERE id = 1"
            )
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            paused_until = row[0]
            return (
                paused_until.replace(tzinfo=TAIPEI)
                if paused_until.tzinfo is None
                else paused_until.astimezone(TAIPEI)
            )


def pause_manager_for(
    minutes: int,
    now: datetime | None = None,
) -> datetime:
    if type(minutes) is not int or not 1 <= minutes <= MAX_MANAGER_PAUSE_MINUTES:
        raise ValueError(
            f"Pause duration must be between 1 and {MAX_MANAGER_PAUSE_MINUTES} minutes"
        )
    paused_until = _taipei_moment(now) + timedelta(minutes=minutes)
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE manager_control SET paused_until = %s WHERE id = 1",
                (paused_until.replace(tzinfo=None),),
            )
    return paused_until


def resume_manager() -> None:
    with pymysql.connect(**mariadb_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE manager_control SET paused_until = NULL WHERE id = 1"
            )


def is_manager_paused(now: datetime | None = None) -> bool:
    paused_until = get_manager_pause_until()
    return paused_until is not None and _taipei_moment(now) < paused_until


def is_taiwan_trading_window(now: datetime | None = None) -> bool:
    moment = _taipei_moment(now)
    return (
        moment.weekday() < 5
        and MARKET_OPEN <= moment.time().replace(tzinfo=None) <= MARKET_CLOSE
    )


def _taipei_moment(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI)
    return now.astimezone(TAIPEI)


def execute_simulated_order(
    *,
    symbol: str,
    market_signal: str,
    action: str,
    lots: int,
    price: float,
    rationale: str,
    now: datetime | None = None,
) -> dict:
    moment = _taipei_moment(now)
    if not is_taiwan_trading_window(moment):
        raise ValueError("Simulated orders are only allowed Mon-Fri, 09:00-13:30 Taipei time")
    if action not in {"ADD", "REDUCE"}:
        raise ValueError("Only ADD or REDUCE decisions can create simulated orders")
    if market_signal not in {"ADD", "REDUCE", "HOLD"}:
        raise ValueError(f"Invalid market signal: {market_signal}")
    if not isinstance(lots, int) or lots < 1:
        raise ValueError("Order size must be a positive whole number of lots")
    if not rationale.strip():
        raise ValueError("A decision rationale is required for every simulated order")

    shares = lots * SHARES_PER_LOT
    order_date = moment.date()
    decimal_price = Decimal(str(price))
    if decimal_price <= 0:
        raise ValueError("Simulated order price must be positive")

    with pymysql.connect(**mariadb_config()) as connection:
        connection.begin()
        try:
            with connection.cursor(DictCursor) as cursor:
                cursor.execute(
                    "SELECT cash_balance FROM simulation_account WHERE id = 1 FOR UPDATE"
                )
                account = cursor.fetchone()
                if account is None:
                    raise RuntimeError("Simulation account is not initialized")
                cash = Decimal(account["cash_balance"])

                cursor.execute(
                    "SELECT paused_until FROM manager_control WHERE id = 1 FOR UPDATE"
                )
                control = cursor.fetchone()
                if control is not None and control["paused_until"] is not None:
                    paused_until = control["paused_until"].replace(tzinfo=TAIPEI)
                    if moment < paused_until:
                        raise ValueError(
                            f"Portfolio manager is paused until {paused_until.isoformat()}"
                        )

                cursor.execute(
                    "SELECT COUNT(*) AS order_count FROM simulated_orders "
                    "WHERE order_date = %s",
                    (order_date,),
                )
                order_count = int(cursor.fetchone()["order_count"])
                if order_count >= MAX_DAILY_ORDERS:
                    raise ValueError("Daily simulated order limit of 3 has been reached")

                cursor.execute(
                    "SELECT quantity, average_cost, currency FROM holdings "
                    "WHERE symbol = %s FOR UPDATE",
                    (symbol.upper(),),
                )
                holding = cursor.fetchone()
                quantity_before = (
                    Decimal(holding["quantity"]) if holding else Decimal("0")
                )
                average_cost = (
                    Decimal(holding["average_cost"]) if holding else Decimal("0")
                )
                trade_value = decimal_price * shares

                if action == "ADD":
                    if trade_value > cash:
                        raise ValueError(
                            f"Insufficient simulated cash: need {trade_value}, have {cash}"
                        )
                    quantity_after = quantity_before + shares
                    average_cost_after = (
                        (quantity_before * average_cost + trade_value) / quantity_after
                    )
                    realized_pnl = Decimal("0")
                    cash_after = cash - trade_value
                else:
                    if shares > quantity_before:
                        raise ValueError(
                            f"Cannot reduce {shares} shares; only {quantity_before} are held"
                        )
                    quantity_after = quantity_before - shares
                    average_cost_after = average_cost
                    realized_pnl = (decimal_price - average_cost) * shares
                    cash_after = cash + trade_value

                unrealized_pnl_after = (
                    (decimal_price - average_cost_after) * quantity_after
                )
                currency = holding["currency"] if holding else "TWD"
                cursor.execute(
                    """
                    INSERT INTO holdings (symbol, quantity, average_cost, currency)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        quantity = VALUES(quantity),
                        average_cost = VALUES(average_cost),
                        currency = VALUES(currency)
                    """,
                    (
                        symbol.upper(),
                        quantity_after,
                        average_cost_after,
                        currency,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE simulation_account SET cash_balance = %s WHERE id = 1
                    """,
                    (cash_after,),
                )
                cursor.execute(
                    """
                    INSERT INTO simulated_orders (
                        order_date, symbol, market_signal, action, lots, shares,
                        price, gross_realized_pnl, unrealized_pnl_after,
                        cash_after, rationale, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        order_date,
                        symbol.upper(),
                        market_signal,
                        action,
                        lots,
                        shares,
                        decimal_price,
                        realized_pnl,
                        unrealized_pnl_after,
                        cash_after,
                        rationale,
                        moment.replace(tzinfo=None),
                    ),
                )
                order_id = int(cursor.lastrowid)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "order_id": order_id,
        "symbol": symbol.upper(),
        "action": action,
        "lots": lots,
        "shares": shares,
        "price": float(decimal_price),
        "gross_realized_pnl": float(realized_pnl),
        "unrealized_pnl_after": float(unrealized_pnl_after),
        "cash_after": float(cash_after),
        "quantity_after": float(quantity_after),
    }
