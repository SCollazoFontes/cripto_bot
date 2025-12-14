#!/usr/bin/env python3
"""
Construye/actualiza un dataset maestro de **trades** para optimizar estrategias
y construir micro-velas a partir del mismo origen.

Modo Binance trades (default):
    python -m tools.data.update_master_dataset \
        --symbol BTCUSDT \
        --mode binance_trades \
        --max-days 365 \
        --chunk-minutes 240 \
        --out data/datasets/BTCUSDT_master.csv

Modo local (fallback):
    python -m tools.data.update_master_dataset \
        --mode local \
        --sources "data/raw_trades/*.csv" \
        --out data/datasets/BTCUSDT_master.csv
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
import glob
import json
from pathlib import Path
import time

from binance.client import Client
import pandas as pd

REQUIRED_COLS = ["timestamp", "price", "qty", "is_buyer_maker"]
DEFAULT_SOURCES = ["data/raw_trades/*.csv"]


# --------------------------- Utilidades comunes -----------------------------
def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    key = "agg_trade_id" if "agg_trade_id" in df.columns else "timestamp"
    df = df.sort_values(key).drop_duplicates(subset=[key], keep="last")
    df.reset_index(drop=True, inplace=True)
    return df


def _to_timestamp(value: str | float | int | None, *, unit: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(float(value) * (1000 if unit == "ms" else 1))
    text = str(value).strip()
    if text.isdigit():
        return int(float(text) * (1000 if unit == "ms" else 1))
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    epoch = dt.replace(tzinfo=UTC).timestamp()
    return int(epoch * (1000 if unit == "ms" else 1))


def _write_summary(out_path: Path, df: pd.DataFrame, extra: dict[str, float | int | str]) -> None:
    summary = {
        "rows": int(len(df)),
        "timestamp_min": float(df["timestamp"].min()),
        "timestamp_max": float(df["timestamp"].max()),
        "from": datetime.fromtimestamp(float(df["timestamp"].min()), tz=UTC).isoformat(),
        "to": datetime.fromtimestamp(float(df["timestamp"].max()), tz=UTC).isoformat(),
        "out_path": str(out_path),
    }
    summary.update(extra)
    summary_path = out_path.with_suffix(".json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


# --------------------------- Modo local -------------------------------------
def _expand_sources(patterns: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        for match in glob.glob(pattern):
            p = Path(match)
            if p.is_file():
                paths.append(p)
    unique = sorted({path.resolve() for path in paths})
    return unique


def _read_csv(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] No se pudo leer {path}: {exc}")
        return None
    missing = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing:
        print(f"[WARN] {path} no tiene columnas requeridas {missing}, se omite.")
        return None
    df = df.copy()
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["source"] = str(path)
    return df


def _concat_sources(paths: Iterable[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        df = _read_csv(path)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        raise ValueError("No se pudo cargar ningún CSV válido. Revisa los patrones de entrada.")
    merged = pd.concat(frames, ignore_index=True)
    return _deduplicate(merged)


def _trim_days(df: pd.DataFrame, max_days: float | None) -> pd.DataFrame:
    if not max_days or max_days <= 0:
        return df
    ts_max = float(df["timestamp"].max())
    seconds = max_days * 86400.0
    cutoff = ts_max - seconds
    trimmed = df[df["timestamp"] >= cutoff].copy()
    trimmed.reset_index(drop=True, inplace=True)
    return trimmed


def _load_existing_dataset(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] No se pudo leer dataset existente {path}: {exc}")
        return None
    if "timestamp" not in df.columns:
        return None
    df = df.copy()
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        return None
    return _deduplicate(df)


def build_dataset_local(
    sources: Sequence[str],
    out_path: Path,
    *,
    max_days: float | None,
) -> pd.DataFrame:
    paths = _expand_sources(sources)
    if not paths:
        raise FileNotFoundError(f"No se encontraron CSV con los patrones: {sources}")
    print(f"[INFO] Lectura de {len(paths)} archivos locales...")
    merged = _concat_sources(paths)
    trimmed = _trim_days(merged, max_days)
    return trimmed


# --------------------------- Modo Binance (agg trades) ----------------------
def _fetch_trades_window(
    client: Client,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    max_retries: int = 5,
) -> list[dict]:
    data: list[dict] = []
    current = start_ms
    while current < end_ms:
        retries = 0
        while True:
            try:
                batch = client.get_aggregate_trades(
                    symbol=symbol,
                    startTime=current,
                    endTime=end_ms,
                    limit=1000,
                )
                break
            except Exception as exc:  # noqa: BLE001
                retries += 1
                if retries >= max_retries:
                    raise RuntimeError(
                        f"Binance aggTrades falló repetidamente en rango "
                        f"{datetime.fromtimestamp(current/1000, tz=UTC)}"
                    ) from exc
                print(f"[WARN] Reintentando aggTrades ({retries}/{max_retries}): {exc}")
                time.sleep(1.5 * retries)
        if not batch:
            break
        data.extend(batch)
        last_ts = int(batch[-1]["T"])
        if last_ts >= end_ms or len(batch) < 1000:
            break
        current = last_ts + 1
        time.sleep(0.05)
    return data


def build_dataset_binance_trades(
    symbol: str,
    out_path: Path,
    *,
    max_days: float | None,
    start: str | float | int | None,
    end: str | float | int | None,
    chunk_minutes: int,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> pd.DataFrame:
    client = Client(api_key, api_secret, requests_params={"timeout": 20})
    end_ms = _to_timestamp(end, unit="ms") or int(time.time() * 1000)
    existing = _load_existing_dataset(out_path)
    derived_start: int | None = None
    if existing is not None and not existing.empty:
        derived_start = int(float(existing["timestamp"].max()) * 1000) + 1
    start_ms = _to_timestamp(start, unit="ms") if start else None
    if start_ms is None:
        if derived_start is not None:
            start_ms = derived_start
        elif max_days and max_days > 0:
            start_ms = end_ms - int(max_days * 86400 * 1000)
        else:
            start_ms = end_ms - int(365 * 86400 * 1000)
    else:
        if derived_start is not None:
            start_ms = max(start_ms, derived_start)
    if start_ms >= end_ms:
        if existing is not None:
            print("[INFO] Dataset maestro ya está actualizado; no se descargan trades nuevos.")
            return _trim_days(existing, max_days)
        raise RuntimeError("Rango solicitado vacío; ajusta start/end o max_days.")
    print(
        f"[INFO] Descargando trades agregados {symbol} "
        f"desde {datetime.fromtimestamp(start_ms/1000, tz=UTC)} "
        f"hasta {datetime.fromtimestamp(end_ms/1000, tz=UTC)} "
        f"en chunks de {chunk_minutes} minutos"
    )
    chunk_ms = max(1, chunk_minutes) * 60 * 1000
    records: list[dict] = []
    chunk_start = start_ms
    chunk_idx = 0
    while chunk_start < end_ms:
        chunk_end = min(end_ms, chunk_start + chunk_ms)
        chunk_idx += 1
        print(
            f"[INFO] Chunk #{chunk_idx}: {datetime.fromtimestamp(chunk_start/1000, tz=UTC)}"
            f" → {datetime.fromtimestamp(chunk_end/1000, tz=UTC)}"
        )
        raw = _fetch_trades_window(client, symbol, chunk_start, chunk_end)
        print(f"[INFO]   Trades descargados: {len(raw)}")
        if raw:
            for item in raw:
                records.append(
                    {
                        "agg_trade_id": int(item["a"]),
                        "price": float(item["p"]),
                        "qty": float(item["q"]),
                        "timestamp": float(item["T"]) / 1000.0,
                        "is_buyer_maker": bool(item["m"]),
                        "is_best_match": bool(item["M"]),
                        "first_trade_id": int(item["f"]),
                        "last_trade_id": int(item["l"]),
                        "dollar_value": float(item["p"]) * float(item["q"]),
                    }
                )
            last_chunk_ts = int(raw[-1]["T"]) + 1
            chunk_start = min(end_ms, max(chunk_start + 1, last_chunk_ts))
        else:
            chunk_start = chunk_end
    if not records:
        if existing is not None:
            print("[INFO] Binance no devolvió trades nuevos; manteniendo dataset actual.")
            return _trim_days(existing, max_days)
        raise RuntimeError("Binance no devolvió datos; verifica el símbolo/rango.")
    df_out = pd.DataFrame.from_records(records)
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, df_out], ignore_index=True)
        combined = _deduplicate(combined)
    else:
        combined = df_out
    combined = _trim_days(combined, max_days)
    return combined


# --------------------------- CLI --------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Construye/actualiza un dataset maestro de trades."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo base (ej: BTCUSDT).")
    parser.add_argument(
        "--mode",
        choices=["binance_trades", "local"],
        default="binance_trades",
        help="Fuente de datos.",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=DEFAULT_SOURCES,
        help="Globs de CSV a combinar (modo local).",
    )
    parser.add_argument(
        "--start",
        help="Inicio del rango (epoch segundos/ms o ISO8601). Por defecto: end - max_days.",
    )
    parser.add_argument(
        "--end",
        help="Fin del rango (epoch segundos/ms o ISO8601). Por defecto: ahora.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Ruta del CSV maestro. Por defecto data/datasets/<symbol>_master.csv",
    )
    parser.add_argument(
        "--max-days",
        type=float,
        default=365.0,
        help="Limite de datos en días (Modo Binance usa esto si no se especifica start).",
    )
    parser.add_argument(
        "--chunk-minutes",
        type=int,
        default=240,
        help="Duración máxima por chunk al descargar trades (para evitar timeouts).",
    )
    parser.add_argument(
        "--api-key", default=None, help="API key de Binance (opcional para datos públicos)."
    )
    parser.add_argument("--api-secret", default=None, help="API secret de Binance (opcional).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbol = args.symbol.upper()
    out_path = Path(args.out) if args.out else Path("data/datasets") / f"{symbol}_master.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "binance_trades":
        df = build_dataset_binance_trades(
            symbol,
            out_path,
            max_days=args.max_days,
            start=args.start,
            end=args.end,
            chunk_minutes=args.chunk_minutes,
            api_key=args.api_key,
            api_secret=args.api_secret,
        )
        df.to_csv(out_path, index=False)
        _write_summary(
            out_path,
            df,
            {
                "mode": "binance_trades",
                "symbol": symbol,
                "max_days": args.max_days,
                "chunk_minutes": args.chunk_minutes,
            },
        )
    else:
        df = build_dataset_local(
            args.sources,
            out_path,
            max_days=args.max_days,
        )
        df.to_csv(out_path, index=False)
        _write_summary(
            out_path,
            df,
            {
                "mode": "local",
                "sources": len(_expand_sources(args.sources)),
                "max_days": args.max_days,
            },
        )
    print(f"[INFO] Dataset maestro actualizado en {out_path}")


if __name__ == "__main__":
    main()
