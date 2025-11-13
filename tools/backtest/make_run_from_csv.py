# tools/make_run_from_csv.py
"""
Crea un run reproducible (carpeta en runs/) a partir de un CSV de entrada.

- Normaliza columnas a: t, open, high, low, close, volume.
- Puede limitar filas y sintetizar timestamps si faltan.
- Escribe:
    - runs/<UTC tag>/data.csv
    - runs/<UTC tag>/manifest.json
    - runs/<UTC tag>/quality.json  (info básica del dataset)

Uso:
  python -m tools.make_run_from_csv --file data/foo.csv
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd

# ---------------------------- Utilidades básicas -----------------------------


def _utc_tag() -> str:
    """Devuelve una etiqueta UTC compacta, p.ej. 20251105T153000Z."""
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    Detecta nombres de columnas relevantes en el CSV de entrada y devuelve
    un mapeo a nombres canónicos: t, open, high, low, close, volume.
    No falla si falta alguna; las ausentes no se incluyen en el mapeo.
    """
    lower = {c.strip().lower(): c for c in df.columns}
    out: dict[str, str] = {}

    # tiempo
    for cand in ("t", "ts", "timestamp", "time", "datetime", "t_close", "end_time"):
        if cand in lower:
            out["t"] = lower[cand]
            break

    # OHLCV
    for key, cands in [
        ("open", ("open", "o")),
        ("high", ("high", "h")),
        ("low", ("low", "l")),
        ("close", ("close", "c", "price", "last", "px")),
        ("volume", ("volume", "v", "qty", "size", "amount")),
    ]:
        for cand in cands:
            if cand in lower:
                out[key] = lower[cand]
                break

    return out


def _normalize_df(
    df: pd.DataFrame,
    mapping: dict[str, str],
    *,
    synthetic_ts_ms: int = 0,
    synthetic_base_ms: int | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """
    Renombra y selecciona columnas. Si no hay 't' y se especifica synthetic_ts_ms,
    genera una serie 't' con paso fijo en milisegundos.
    """
    work = df.copy()

    # Limitar filas si procede
    if max_rows is not None and max_rows > 0:
        work = work.iloc[:max_rows, :]

    # Si no hay 't' y se pide sintético, lo creamos
    if "t" not in mapping and synthetic_ts_ms > 0:
        n = len(work)
        if n == 0:
            return pd.DataFrame(columns=["t", "open", "high", "low", "close", "volume"])
        base = synthetic_base_ms if synthetic_base_ms is not None else 0
        tvals = [base + i * int(synthetic_ts_ms) for i in range(n)]
        work = work.reset_index(drop=True)
        work.insert(0, "t", tvals)
        mapping = {"t": "t", **mapping}

    # Renombrar y seleccionar
    rename_map = {src: dst for dst, src in mapping.items()}
    work = work.rename(columns=rename_map)

    cols: list[str] = ["t", "open", "high", "low", "close", "volume"]
    keep = [c for c in cols if c in work.columns]
    work = work[keep]

    # Tipos numéricos
    if "t" in work.columns:
        work["t"] = pd.to_numeric(work["t"], errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        if c in work.columns:
            work[c] = pd.to_numeric(work[c], errors="coerce")

    # Filas válidas (al menos t y close)
    need = [c for c in ("t", "close") if c in work.columns]
    work = work.dropna(subset=need).reset_index(drop=True)

    return work


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _write_json(path: Path, obj: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _quality(df: pd.DataFrame) -> dict[str, object]:
    """
    Calcula métricas simples de calidad del dataset para inspección rápida.
    """
    out: dict[str, object] = {}
    out["rows"] = int(len(df))
    out["columns"] = list(df.columns)
    out["has_t"] = bool("t" in df.columns)
    out["has_ohlcv"] = bool(all(c in df.columns for c in ("open", "high", "low", "close")))
    out["has_volume"] = bool("volume" in df.columns)

    tmin = int(df["t"].min()) if "t" in df.columns and len(df) else None
    tmax = int(df["t"].max()) if "t" in df.columns and len(df) else None
    out["t_min"] = tmin
    out["t_max"] = tmax
    out["first_rows_preview"] = df.head(3).to_dict(orient="records")
    return out


# --------------------------------- CLI --------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crea un run desde un CSV.")
    p.add_argument("--file", required=True, help="CSV de entrada.")
    p.add_argument("--run-dir", default=None, help="Destino (por defecto runs/<UTC>).")
    p.add_argument("--sep", default=",", help="Separador CSV (por defecto ',').")
    p.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Límite de filas para pruebas (0 = sin límite).",
    )
    p.add_argument(
        "--synthetic-ts-ms",
        type=int,
        default=0,
        help="Paso en ms si falta 't' (0 = no crear).",
    )
    p.add_argument(
        "--synthetic-base-ms",
        type=int,
        default=None,
        help="Base ms para timestamps sintéticos (opcional).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    src = Path(args.file)
    if not src.exists():
        raise FileNotFoundError(f"No existe el archivo: {src}")

    # Leer CSV tolerante
    df = pd.read_csv(src, sep=args.sep, on_bad_lines="skip")
    mapping = _detect_columns(df)

    norm = _normalize_df(
        df,
        mapping,
        synthetic_ts_ms=int(args.synthetic_ts_ms),
        synthetic_base_ms=(
            int(args.synthetic_base_ms) if args.synthetic_base_ms is not None else None
        ),
        max_rows=int(args.max_rows) if int(args.max_rows) > 0 else None,
    )

    # Directorio del run
    run_dir = Path(args.run_dir) if args.run_dir else Path("runs") / _utc_tag()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Escribir artefactos
    _write_csv(run_dir / "data.csv", norm)

    manifest = {
        "created_at": _utc_tag(),
        "source_path": str(src.resolve()),
        "rows": int(len(norm)),
        "columns": list(norm.columns),
    }
    _write_json(run_dir / "manifest.json", manifest)

    quality = _quality(norm)
    _write_json(run_dir / "quality.json", quality)

    print(f"✅ Run creado en: {run_dir}")


if __name__ == "__main__":
    main()
