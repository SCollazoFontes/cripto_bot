from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pandas as pd

from .records import (
    BarRecord,
    EquityRecord,
    FeatureRecord,
    SignalRecord,
    TradeRecord,
)


class DataStorage:
    """
    Sistema de almacenamiento unificado (SQLite backend).

    Uso:
        storage = DataStorage("data/trading_data.db")

        # Guardar trades
        storage.save_trades([trade1, trade2, ...])

        # Guardar bars
        storage.save_bars([bar1, bar2, ...])

        # Query
        df = storage.query_trades(symbol="BTCUSDT", start_ts=..., end_ts=...)
    """

    def __init__(self, db_path: str | Path = "data/trading_data.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _init_database(self) -> None:
        """Crea tablas si no existen."""
        with sqlite3.connect(self.db_path) as conn:
            # Trades
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty REAL NOT NULL,
                    is_buyer_maker INTEGER NOT NULL,
                    run_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")

            # Bars
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    trade_count INTEGER NOT NULL,
                    dollar_value REAL NOT NULL,
                    run_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bars_ts ON bars(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bars_symbol ON bars(symbol)")

            # Features
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    feature_value REAL NOT NULL,
                    run_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_ts ON features(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_name ON features(feature_name)")

            # Signals
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty REAL NOT NULL,
                    reason TEXT NOT NULL,
                    metadata TEXT,
                    run_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type)")

            # Equity
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    position_qty REAL NOT NULL,
                    cash REAL NOT NULL,
                    equity REAL NOT NULL,
                    run_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_run ON equity(run_id)")

            # Runs metadata
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    symbol TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    params TEXT,
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            conn.commit()

    # ==================== SAVE METHODS ====================

    def save_trades(self, trades: list[TradeRecord]) -> int:
        """Guarda trades. Retorna número de registros insertados."""
        if not trades:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO trades (timestamp, symbol, price, qty, is_buyer_maker, run_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        t.timestamp,
                        t.symbol,
                        t.price,
                        t.qty,
                        1 if t.is_buyer_maker else 0,
                        t.run_id,
                    )
                    for t in trades
                ],
            )
            conn.commit()
            return cursor.rowcount

    def save_bars(self, bars: list[BarRecord]) -> int:
        """Guarda bars. Retorna número de registros insertados."""
        if not bars:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO bars (timestamp, symbol, open, high, low, close,
                                  volume, trade_count, dollar_value, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        b.timestamp,
                        b.symbol,
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                        b.trade_count,
                        b.dollar_value,
                        b.run_id,
                    )
                    for b in bars
                ],
            )
            conn.commit()
            return cursor.rowcount

    def save_features(self, features: list[FeatureRecord]) -> int:
        """Guarda features calculados."""
        if not features:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO features (timestamp, symbol, feature_name, feature_value, run_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (f.timestamp, f.symbol, f.feature_name, f.feature_value, f.run_id)
                    for f in features
                ],
            )
            conn.commit()
            return cursor.rowcount

    def save_signals(self, signals: list[SignalRecord]) -> int:
        """Guarda señales de trading."""
        if not signals:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO signals (timestamp, symbol, signal_type, side, price, qty,
                                     reason, metadata, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.timestamp,
                        s.symbol,
                        s.signal_type,
                        s.side,
                        s.price,
                        s.qty,
                        s.reason,
                        s.metadata,
                        s.run_id,
                    )
                    for s in signals
                ],
            )
            conn.commit()
            return cursor.rowcount

    def save_equity(self, equity_points: list[EquityRecord]) -> int:
        """Guarda puntos de equity curve."""
        if not equity_points:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO equity (timestamp, symbol, price, position_qty, cash, equity, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (e.timestamp, e.symbol, e.price, e.position_qty, e.cash, e.equity, e.run_id)
                    for e in equity_points
                ],
            )
            conn.commit()
            return cursor.rowcount

    def save_run_metadata(
        self,
        run_id: str,
        started_at: float,
        symbol: str,
        strategy: str,
        params: dict | None = None,
        finished_at: float | None = None,
        summary: dict | None = None,
    ) -> None:
        """Guarda metadata de un run."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                (run_id, started_at, finished_at, symbol, strategy, params, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    started_at,
                    finished_at,
                    symbol,
                    strategy,
                    json.dumps(params) if params else None,
                    json.dumps(summary) if summary else None,
                ),
            )
            conn.commit()

    # ==================== QUERY METHODS ====================

    def query_trades(
        self,
        symbol: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Query trades con filtros opcionales."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[str | float] = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_ts:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)

        query += " ORDER BY timestamp"

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_bars(
        self,
        symbol: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Query bars con filtros opcionales."""
        query = "SELECT * FROM bars WHERE 1=1"
        params: list[str | float] = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_ts:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)

        query += " ORDER BY timestamp"

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_features(
        self,
        feature_names: list[str] | None = None,
        symbol: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> pd.DataFrame:
        """Query features pivoteado (columnas = feature names)."""
        query = "SELECT timestamp, symbol, feature_name, feature_value FROM features WHERE 1=1"
        params: list[str | float] = []

        if feature_names:
            placeholders = ",".join("?" * len(feature_names))
            query += f" AND feature_name IN ({placeholders})"
            params.extend(feature_names)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_ts:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts:
            query += " AND timestamp <= ?"
            params.append(end_ts)

        query += " ORDER BY timestamp"

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if df.empty:
            return df

        return df.pivot_table(
            index=["timestamp", "symbol"],
            columns="feature_name",
            values="feature_value",
            aggfunc="first",
        ).reset_index()

    def query_equity(self, run_id: str) -> pd.DataFrame:
        """Query equity curve de un run específico."""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM equity WHERE run_id = ? ORDER BY timestamp", conn, params=[run_id]
            )

    def get_run_metadata(self, run_id: str) -> dict | None:
        """Obtiene metadata de un run."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE run_id = ?", [run_id])
            row = cursor.fetchone()
            if not row:
                return None

            cols = [desc[0] for desc in cursor.description]
            data = dict(zip(cols, row, strict=False))

            if data.get("params"):
                data["params"] = json.loads(data["params"])
            if data.get("summary"):
                data["summary"] = json.loads(data["summary"])

            return data

    def list_runs(self, strategy: str | None = None, limit: int = 50) -> pd.DataFrame:
        """Lista runs disponibles."""
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[str] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY started_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ==================== EXPORT METHODS ====================

    def export_to_parquet(self, table: str, output_path: str | Path) -> None:
        """Exporta tabla completa a Parquet (para ML)."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)

        df.to_parquet(output_path, index=False, compression="snappy")
        print(f"✅ Exportado {len(df):,} registros a {output_path}")

    def get_stats(self) -> dict:
        """Estadísticas generales de la base de datos."""
        with sqlite3.connect(self.db_path) as conn:
            stats: dict[str, float | int] = {}
            for table in ["trades", "bars", "features", "signals", "equity", "runs"]:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                stats[table] = cursor.fetchone()[0]

            stats["db_size_mb"] = self.db_path.stat().st_size / (1024 * 1024)

        return stats
