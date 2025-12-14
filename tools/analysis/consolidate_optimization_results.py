#!/usr/bin/env python3
"""
Consolida todos los resultados de optimización en un CSV único para análisis.

Recorre todos los archivos opt_summary.json en runs_opt/ y genera un CSV
con todas las métricas y parámetros, facilitando el análisis comparativo.

Uso:
    python -m tools.analysis.consolidate_optimization_results \
        --input runs_opt \
        --output runs_opt/consolidated_results.csv

El CSV resultante incluye:
    - Estrategia, optimizer, builder, ventana
    - Todos los parámetros de la estrategia
    - Métricas clave: score, total_return, trades, equity_final
    - Ruta del run para debugging
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def find_all_summaries(root_dir: Path) -> list[Path]:
    """Encuentra todos los archivos opt_summary.json recursivamente."""
    return sorted(root_dir.rglob("opt_summary.json"))


def extract_flat_row(summary_path: Path) -> dict[str, Any]:
    """
    Lee un opt_summary.json y lo aplana en un dict para CSV.

    Retorna un dict con:
        - run_dir: ruta relativa al run
        - strategy, optimizer, builder, window_label
        - score, total_return, trades, equity_final, bars_processed
        - param_* : todos los parámetros de la estrategia
    """
    with summary_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    row: dict[str, Any] = {}

    # Metadatos del run
    row["run_dir"] = str(summary_path.parent)
    row["strategy"] = data.get("strategy", "unknown")
    row["optimizer"] = data.get("optimizer", "unknown")

    # Ventana
    window = data.get("window", {})
    row["window_label"] = window.get("label", "unknown")
    row["window_start_ts"] = window.get("start_ts", 0)
    row["window_end_ts"] = window.get("end_ts", 0)

    # Detectar builder desde la ruta (ej: runs_opt/hybrid_100ticks_all/momentum/...)
    parts = Path(row["run_dir"]).parts
    # Buscar el builder (normalmente el primer directorio después de runs_opt)
    builder = "unknown"
    if "runs_opt" in parts:
        idx = parts.index("runs_opt")
        if idx + 1 < len(parts):
            builder = parts[idx + 1]
    row["builder"] = builder

    # Score y métricas principales
    row["score"] = data.get("score", 0.0)
    metrics = data.get("metrics", {})
    row["total_return"] = metrics.get("total_return", 0.0)
    row["trades"] = metrics.get("trades", 0)
    row["equity_final"] = metrics.get("equity_final", 100.0)
    row["bars_processed"] = metrics.get("bars_processed", 0)

    # Métricas adicionales si existen
    row["penalized_reason"] = metrics.get("penalized_reason", "")

    # Parámetros de la estrategia (prefijo param_)
    params = data.get("params", {})
    for key, value in params.items():
        # Convertir valores a strings legibles
        if isinstance(value, float):
            row[f"param_{key}"] = f"{value:.6f}"
        else:
            row[f"param_{key}"] = str(value)

    return row


def consolidate_results(input_dir: Path, output_csv: Path) -> None:
    """
    Lee todos los opt_summary.json y genera un CSV consolidado.
    """
    summaries = find_all_summaries(input_dir)

    if not summaries:
        print(f"❌ No se encontraron archivos opt_summary.json en {input_dir}")
        return

    print(f"📊 Encontrados {len(summaries)} archivos de optimización")

    # Recolectar todas las filas
    rows: list[dict[str, Any]] = []
    param_keys: set[str] = set()

    for summary_path in summaries:
        try:
            row = extract_flat_row(summary_path)
            rows.append(row)
            # Recolectar todas las claves de parámetros para el header
            param_keys.update(k for k in row.keys() if k.startswith("param_"))
        except Exception as e:
            print(f"⚠️  Error procesando {summary_path}: {e}")
            continue

    if not rows:
        print("❌ No se pudieron procesar resultados")
        return

    # Definir orden de columnas
    base_cols = [
        "strategy",
        "optimizer",
        "builder",
        "window_label",
        "score",
        "total_return",
        "trades",
        "equity_final",
        "bars_processed",
        "penalized_reason",
        "window_start_ts",
        "window_end_ts",
    ]
    param_cols = sorted(param_keys)
    all_cols = base_cols + param_cols + ["run_dir"]

    # Escribir CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"✅ Consolidado guardado en: {output_csv}")
    print(f"📈 Total de experimentos: {len(rows)}")

    # Estadísticas básicas
    scores = [r["score"] for r in rows if isinstance(r.get("score"), (int, float))]
    if scores:
        print("\n📊 Estadísticas de score:")
        print(f"   - Mejor:  {max(scores):.6f}")
        print(f"   - Peor:   {min(scores):.6f}")
        print(f"   - Media:  {sum(scores)/len(scores):.6f}")
        print(f"   - Positivos: {sum(1 for s in scores if s > 0)} / {len(scores)}")

    # Top 10 mejores
    top10 = sorted(rows, key=lambda r: r.get("score", -1e9), reverse=True)[:10]
    print("\n🏆 Top 10 configuraciones:")
    for i, r in enumerate(top10, 1):
        print(
            f"   {i}. score={r['score']:.6f} | trades={r['trades']} | "
            f"builder={r['builder']} | window={r['window_label']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolida resultados de optimización en un CSV único"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("runs_opt"),
        help="Directorio raíz con resultados de optimización",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs_opt/consolidated_results.csv"),
        help="Archivo CSV de salida",
    )
    args = parser.parse_args()

    consolidate_results(args.input, args.output)


if __name__ == "__main__":
    main()
