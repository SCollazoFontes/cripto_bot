#!/usr/bin/env python3
"""
Limpia runs de optimización según criterios configurables.

Casos de uso:
  1. Eliminar runs penalizados (score muy negativo, ej: -1000000)
  2. Eliminar runs con pocos trades
  3. Eliminar runs con score muy bajo
  4. Mantener solo el top N de cada ventana

Uso:
    # Ver qué se eliminaría (dry-run):
    python -m tools.analysis.cleanup_optimization_runs --dry-run

    # Eliminar runs penalizados:
    python -m tools.analysis.cleanup_optimization_runs --remove-penalized

    # Eliminar runs con <5 trades:
    python -m tools.analysis.cleanup_optimization_runs --min-trades 5

    # Mantener solo top 10 por ventana:
    python -m tools.analysis.cleanup_optimization_runs --keep-top-n 10
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def find_all_summaries(root_dir: Path) -> list[Path]:
    """Encuentra todos los archivos opt_summary.json recursivamente."""
    return sorted(root_dir.rglob("opt_summary.json"))


def should_remove(
    summary_path: Path,
    *,
    remove_penalized: bool,
    min_trades: int | None,
    min_score: float | None,
) -> tuple[bool, str]:
    """
    Determina si un run debe ser eliminado.

    Returns:
        (should_remove, reason)
    """
    try:
        with summary_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return True, f"Error leyendo JSON: {e}"

    score = data.get("score", 0.0)
    metrics = data.get("metrics", {})
    trades = metrics.get("trades", 0)

    # Check penalizado
    if remove_penalized and score < -100_000:
        return True, f"Penalizado (score={score:.0f})"

    # Check min trades
    if min_trades is not None and trades < min_trades:
        return True, f"Pocos trades ({trades} < {min_trades})"

    # Check min score
    if min_score is not None and score < min_score:
        return True, f"Score bajo ({score:.6f} < {min_score:.6f})"

    return False, ""


def cleanup_runs(
    root_dir: Path,
    *,
    dry_run: bool = True,
    remove_penalized: bool = False,
    min_trades: int | None = None,
    min_score: float | None = None,
    keep_top_n: int | None = None,
) -> None:
    """
    Limpia runs de optimización según criterios.
    """
    summaries = find_all_summaries(root_dir)

    if not summaries:
        print(f"❌ No se encontraron archivos opt_summary.json en {root_dir}")
        return

    print(f"📊 Encontrados {len(summaries)} runs de optimización")
    print()

    to_remove: list[tuple[Path, str]] = []
    to_keep: list[tuple[Path, float]] = []

    # Primera pasada: identificar candidatos a eliminación
    for summary_path in summaries:
        should_rm, reason = should_remove(
            summary_path,
            remove_penalized=remove_penalized,
            min_trades=min_trades,
            min_score=min_score,
        )

        if should_rm:
            to_remove.append((summary_path, reason))
        else:
            # Guardar para keep_top_n
            try:
                with summary_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                score = data.get("score", 0.0)
                to_keep.append((summary_path, score))
            except Exception:
                pass

    # Segunda pasada: keep_top_n por ventana/builder
    if keep_top_n is not None:
        # Agrupar por ventana y builder
        from collections import defaultdict

        groups: dict[str, list[tuple[Path, float]]] = defaultdict(list)

        for path, score in to_keep:
            # Extraer ventana y builder de la ruta
            parts = path.parts
            if "runs_opt" in parts:
                idx = parts.index("runs_opt")
                if idx + 4 < len(parts):
                    builder = parts[idx + 1]
                    strategy = parts[idx + 2]
                    optimizer = parts[idx + 3]
                    window = parts[idx + 4]
                    key = f"{builder}/{strategy}/{optimizer}/{window}"
                    groups[key].append((path, score))

        # Marcar para eliminación los que no estén en top N
        for key, items in groups.items():
            sorted_items = sorted(items, key=lambda x: x[1], reverse=True)
            if len(sorted_items) > keep_top_n:
                for path, score in sorted_items[keep_top_n:]:
                    reason = f"No está en top {keep_top_n} de {key}"
                    to_remove.append((path, reason))

    # Resumen
    print(f"🗑️  Runs a eliminar: {len(to_remove)}")
    print(f"✅ Runs a mantener: {len(summaries) - len(to_remove)}")
    print()

    if to_remove:
        # Agrupar por razón
        from collections import Counter

        reasons = Counter(reason for _, reason in to_remove)
        print("📋 Distribución por razón:")
        for reason, count in reasons.most_common():
            print(f"   - {reason}: {count}")
        print()

    # Eliminar
    if dry_run:
        print("🔍 DRY RUN: No se eliminará nada (usa --no-dry-run para ejecutar)")
        if to_remove:
            print("\nEjemplos de lo que se eliminaría:")
            for path, reason in to_remove[:5]:
                print(f"   - {path.parent} ({reason})")
    else:
        print("🔥 Eliminando runs...")
        removed = 0
        failed = 0

        for summary_path, reason in to_remove:
            run_dir = summary_path.parent
            try:
                shutil.rmtree(run_dir)
                removed += 1
                if removed % 100 == 0:
                    print(f"   Progreso: {removed}/{len(to_remove)}")
            except Exception as e:
                print(f"   ⚠️  Error eliminando {run_dir}: {e}")
                failed += 1

        print(f"\n✅ Eliminados: {removed}")
        if failed:
            print(f"⚠️  Fallidos: {failed}")

    # Estadísticas finales
    total_size_before = (
        sum(sum(f.stat().st_size for f in p.parent.rglob("*") if f.is_file()) for p, _ in to_remove)
        if not dry_run
        else 0
    )

    if total_size_before > 0:
        print(f"\n💾 Espacio liberado: {total_size_before / 1024 / 1024:.2f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Limpia runs de optimización según criterios")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("runs_opt"),
        help="Directorio raíz con resultados de optimización",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Solo mostrar qué se eliminaría sin hacerlo",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Ejecutar la eliminación realmente",
    )
    parser.add_argument(
        "--remove-penalized",
        action="store_true",
        help="Eliminar runs penalizados (score < -100000)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        help="Eliminar runs con menos de N trades",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        help="Eliminar runs con score menor a N",
    )
    parser.add_argument(
        "--keep-top-n",
        type=int,
        help="Mantener solo los top N runs por ventana/builder",
    )

    args = parser.parse_args()

    if not any([args.remove_penalized, args.min_trades, args.min_score, args.keep_top_n]):
        print("⚠️  No se especificó ningún criterio de limpieza.")
        print("   Usa --remove-penalized, --min-trades, --min-score, o --keep-top-n")
        return

    cleanup_runs(
        args.input,
        dry_run=args.dry_run,
        remove_penalized=args.remove_penalized,
        min_trades=args.min_trades,
        min_score=args.min_score,
        keep_top_n=args.keep_top_n,
    )


if __name__ == "__main__":
    main()
