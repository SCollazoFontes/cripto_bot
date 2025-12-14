#!/bin/bash
# Mantenimiento automático del proyecto cripto_bot
# Ejecuta limpieza de archivos temporales y consolida resultados

set -e  # Salir si hay error

echo "🧹 Iniciando mantenimiento del proyecto cripto_bot..."
echo ""

# 1. Limpiar archivos temporales
echo "📦 Limpiando archivos temporales..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "*.pyo" -delete 2>/dev/null || true
find . -name "*~" -delete 2>/dev/null || true
find . -name ".DS_Store" -delete 2>/dev/null || true
echo "   ✅ Cachés eliminados"
echo ""

# 2. Consolidar resultados de optimización (si existen)
if [ -d "runs_opt" ] && [ -n "$(find runs_opt -name 'opt_summary.json' -print -quit)" ]; then
    echo "📊 Consolidando resultados de optimización..."
    source activate.sh > /dev/null 2>&1 || true
    python -m tools.analysis.consolidate_optimization_results \
        --input runs_opt \
        --output runs_opt/consolidated_results.csv 2>&1 | grep -E "(Encontrados|Consolidado|Top 10|Estadísticas)" || true
    echo ""
fi

# 3. Reportar espacio ocupado
echo "💾 Espacio ocupado por directorios principales:"
du -sh data/datasets 2>/dev/null || echo "   data/datasets: no existe"
du -sh runs 2>/dev/null || echo "   runs: no existe"
du -sh runs_opt 2>/dev/null || echo "   runs_opt: no existe"
du -sh runs_opt_vol 2>/dev/null || echo "   runs_opt_vol: no existe"
echo ""

# 4. Mostrar estadísticas de optimización (si existe el CSV)
if [ -f "runs_opt/consolidated_results.csv" ]; then
    echo "📈 Estadísticas rápidas de optimización:"
    total_runs=$(tail -n +2 runs_opt/consolidated_results.csv | wc -l | xargs)
    echo "   Total de runs: $total_runs"
    
    # Contar runs positivos vs negativos
    if command -v python3 &> /dev/null; then
        source activate.sh > /dev/null 2>&1 || true
        python3 << EOF
import csv
with open("runs_opt/consolidated_results.csv", "r") as f:
    reader = csv.DictReader(f)
    scores = [float(row["score"]) for row in reader if row.get("score")]
    if scores:
        positive = sum(1 for s in scores if s > 0)
        negative = sum(1 for s in scores if s < 0)
        penalized = sum(1 for s in scores if s < -100000)
        print(f"   Runs positivos: {positive} ({100*positive/len(scores):.1f}%)")
        print(f"   Runs negativos: {negative} ({100*negative/len(scores):.1f}%)")
        print(f"   Runs penalizados: {penalized} ({100*penalized/len(scores):.1f}%)")
        if positive > 0:
            best = max(scores)
            print(f"   Mejor score: {best:.6f}")
EOF
    fi
    echo ""
fi

# 5. Sugerencias de limpieza
if [ -d "runs_opt" ]; then
    penalized_count=$(find runs_opt -name "opt_summary.json" -exec grep -l '"score": -1000000' {} \; 2>/dev/null | wc -l | xargs)
    if [ "$penalized_count" -gt 0 ]; then
        echo "💡 Sugerencia: Hay $penalized_count runs penalizados que podrías eliminar para liberar espacio:"
        echo "   python -m tools.analysis.cleanup_optimization_runs --remove-penalized --no-dry-run"
        echo ""
    fi
fi

echo "✅ Mantenimiento completado"
