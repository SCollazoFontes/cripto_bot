# tools/check_csvfeed.py
"""
Smoke test del CSVFeed:
- Carga el archivo (o el último del patrón).
- Imprime el nº de barras y la primera.
- Verifica presencia de campos clave (open, high, low, close).
- Devuelve exit(1) si algo crítico falla.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Optional

import pandas as pd

from src.data.feeds import CSVFeed
from src.data.validate import validate


def _find_latest_file(directory: str, pattern: Optional[str]) -> str:
    pattern = pattern or "*.csv"
    paths = glob.glob(os.path.join(directory, pattern))
    if not paths:
        raise FileNotFoundError(
            f"No se encontraron archivos en {directory!r} con patrón {pattern!r}"
        )
    paths.sort(key=lambda p: os.path.getmtime(p))
    return paths[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="Ruta a CSV. Si no, busca el último por patrón.")
    ap.add_argument("--dir", default="data/bars_live", help="Carpeta donde buscar el último.")
    ap.add_argument(
        "--pattern", default="btcusdt_volume_qty_*.csv", help="Patrón para buscar el último."
    )
    args = ap.parse_args(argv)

    path = args.file or _find_latest_file(args.dir, args.pattern)
    print(f"📄 Archivo: {path}")

    # 1) Valida (no estricto aquí: queremos ver issues si existieran)
    df = pd.read_csv(path)
    res = validate(
        df,
        require_columns=["t_open", "t_close", "open", "high", "low", "close", "volume"],
        allow_nan_in=["gap_ms"],
    )
    print(f"Validator OK? {res['ok']}. Issues: {[i['code'] for i in res['issues']]}")

    if not res["ok"]:
        print("❌ Datos no válidos para feed.", file=sys.stderr)
        return 1

    # 2) Instancia el feed y emite la primera barra
    feed = CSVFeed(path, validate_on_init=True)
    n = len(feed)
    print(f"🔢 Barras: {n}")
    if n == 0:
        print("❌ El feed no tiene barras.", file=sys.stderr)
        return 1

    first = next(iter(feed))
    # chequeos mínimos de campos
    for k in ("open", "high", "low", "close"):
        if not hasattr(first, k):
            print(f"❌ La primera barra no tiene campo requerido: {k}", file=sys.stderr)
            return 1

    print(
        "🟢 Primera barra (resumen):",
        {
            k: getattr(first, k)
            for k in ("t_open", "t_close", "open", "high", "low", "close")
            if hasattr(first, k)
        },
    )
    print("✅ CSVFeed smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
