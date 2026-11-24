from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "ups_history.db"


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_history_db() -> None:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                underlying_price REAL,
                underlying_quote_time TEXT,
                chain_is_delayed INTEGER,
                regular_session_clock INTEGER,
                contracts_saved INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS option_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                contract_symbol TEXT NOT NULL,
                expiration TEXT NOT NULL,
                dte INTEGER,
                strike REAL NOT NULL,
                bid REAL,
                ask REAL,
                delta REAL,
                volume INTEGER,
                open_interest INTEGER,
                spread_pct REAL,
                option_quote_time_utc TEXT,
                quote_age_minutes REAL,
                multiplier REAL,
                FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE,
                UNIQUE(snapshot_id, contract_symbol)
            );

            CREATE INDEX IF NOT EXISTS idx_option_quotes_symbol
                ON option_quotes(contract_symbol);
            CREATE INDEX IF NOT EXISTS idx_snapshots_captured
                ON snapshots(captured_at_utc);
            """
        )


def _extract_contracts(chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract every standard 100-share UPS call in the returned chain.

    The Schwab client controls the requested expiration window. This function
    intentionally does not apply dashboard strike, OI, spread, or quote-age
    filters, so later analysis can use the complete captured chain.
    """
    results: list[dict[str, Any]] = []
    expiration_map = chain.get("callExpDateMap", {}) or {}

    for expiration_key, strike_map in expiration_map.items():
        for strike_text, contracts in (strike_map or {}).items():
            for contract in contracts or []:
                if contract.get("putCall") != "CALL":
                    continue
                if contract.get("nonStandard", False):
                    continue

                multiplier = _to_float(contract.get("multiplier"), 100.0)
                if multiplier != 100.0:
                    continue

                strike = _to_float(contract.get("strikePrice", strike_text))
                if strike is None:
                    continue

                expiration_raw = contract.get("expirationDate", expiration_key)
                expiration = str(expiration_raw).split("T")[0]
                symbol = str(contract.get("symbol") or "").strip()
                contract_key = symbol or f"UPS:{expiration}:{strike:.2f}:CALL"

                bid = _to_float(contract.get("bid"), 0.0)
                ask = _to_float(contract.get("ask"), 0.0)
                midpoint = ((bid + ask) / 2) if bid is not None and ask is not None and bid > 0 and ask > 0 else None
                spread_pct = None
                if midpoint and ask is not None and bid is not None and ask >= bid:
                    spread_pct = (ask - bid) / midpoint * 100

                quote_ms = _to_int(contract.get("quoteTimeInLong"), 0) or 0
                quote_time_utc = None
                quote_age_minutes = None
                if quote_ms > 0:
                    try:
                        quote_dt = datetime.fromtimestamp(quote_ms / 1000, timezone.utc)
                        quote_time_utc = quote_dt.isoformat()
                        quote_age_minutes = (datetime.now(timezone.utc) - quote_dt).total_seconds() / 60
                    except (ValueError, OSError, OverflowError):
                        pass

                results.append(
                    {
                        "contract_symbol": contract_key,
                        "expiration": expiration,
                        "dte": _to_int(contract.get("daysToExpiration")),
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "delta": _to_float(contract.get("delta")),
                        "volume": _to_int(contract.get("totalVolume"), 0),
                        "open_interest": _to_int(contract.get("openInterest"), 0),
                        "spread_pct": spread_pct,
                        "option_quote_time_utc": quote_time_utc,
                        "quote_age_minutes": quote_age_minutes,
                        "multiplier": multiplier,
                    }
                )

    return results


def record_snapshot(
    chain: dict[str, Any],
    underlying_price: float,
    underlying_quote_time: str | None = None,
    regular_session_clock: bool = False,
    symbol: str = "UPS",
) -> dict[str, int | str]:
    """Persist one manual market-data refresh and all standard calls returned."""
    initialize_history_db()
    contracts = _extract_contracts(chain)
    captured_at = datetime.now(timezone.utc).isoformat()
    delayed_raw = chain.get("isDelayed")
    delayed_value = None if delayed_raw is None else int(bool(delayed_raw))

    with _connect() as connection:
        cursor = connection.execute(
            """INSERT INTO snapshots
               (captured_at_utc, symbol, underlying_price, underlying_quote_time,
                chain_is_delayed, regular_session_clock, contracts_saved)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                captured_at,
                symbol,
                float(underlying_price),
                underlying_quote_time,
                delayed_value,
                int(bool(regular_session_clock)),
                len(contracts),
            ),
        )
        snapshot_id = int(cursor.lastrowid)

        connection.executemany(
            """INSERT INTO option_quotes
               (snapshot_id, contract_symbol, expiration, dte, strike, bid, ask,
                delta, volume, open_interest, spread_pct, option_quote_time_utc,
                quote_age_minutes, multiplier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    item["contract_symbol"],
                    item["expiration"],
                    item["dte"],
                    item["strike"],
                    item["bid"],
                    item["ask"],
                    item["delta"],
                    item["volume"],
                    item["open_interest"],
                    item["spread_pct"],
                    item["option_quote_time_utc"],
                    item["quote_age_minutes"],
                    item["multiplier"],
                )
                for item in contracts
            ],
        )

    return {
        "snapshot_id": snapshot_id,
        "contracts_saved": len(contracts),
        "database_path": str(DB_PATH),
    }


def load_history(symbol: str = "UPS", contract_symbol: str | None = None) -> list[dict[str, Any]]:
    """Return saved option observations, oldest first for time-series charts."""
    initialize_history_db()
    sql = """
        SELECT
            s.id AS snapshot_id,
            s.captured_at_utc,
            s.symbol AS underlying_symbol,
            s.underlying_price,
            s.underlying_quote_time,
            s.chain_is_delayed,
            s.regular_session_clock,
            q.contract_symbol,
            q.expiration,
            q.dte,
            q.strike,
            q.bid,
            q.ask,
            q.delta,
            q.volume,
            q.open_interest,
            q.spread_pct,
            q.option_quote_time_utc,
            q.quote_age_minutes
        FROM option_quotes q
        JOIN snapshots s ON s.id = q.snapshot_id
        WHERE s.symbol = ?
    """
    params: list[Any] = [symbol]
    if contract_symbol:
        sql += " AND q.contract_symbol = ?"
        params.append(contract_symbol)
    sql += " ORDER BY s.captured_at_utc ASC, q.contract_symbol ASC"

    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def get_history_summary(symbol: str = "UPS") -> dict[str, Any]:
    initialize_history_db()
    with _connect() as connection:
        snapshot_row = connection.execute(
            """SELECT COUNT(*) AS snapshot_count,
                      MIN(captured_at_utc) AS first_capture,
                      MAX(captured_at_utc) AS last_capture,
                      SUM(contracts_saved) AS total_contract_observations
               FROM snapshots WHERE symbol = ?""",
            (symbol,),
        ).fetchone()
        contract_row = connection.execute(
            "SELECT COUNT(DISTINCT q.contract_symbol) AS distinct_contracts "
            "FROM option_quotes q JOIN snapshots s ON s.id = q.snapshot_id "
            "WHERE s.symbol = ?",
            (symbol,),
        ).fetchone()

    result = dict(snapshot_row) if snapshot_row else {}
    result["distinct_contracts"] = contract_row["distinct_contracts"] if contract_row else 0
    return result
