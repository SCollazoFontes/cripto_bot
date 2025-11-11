#!/usr/bin/env bash
# activate.sh - Script para activar el entorno cripto_bot y configurar PYTHONPATH
# Uso: source activate.sh

# Activar el entorno conda cripto_bot
if command -v conda &> /dev/null; then
    echo "🔧 Activando entorno conda 'cripto_bot'..."
    conda activate cripto_bot
    
    # Configurar PYTHONPATH para que apunte a src/
    export PYTHONPATH="$(pwd)/src"
    echo "✅ Entorno activado y PYTHONPATH configurado: $PYTHONPATH"
    echo ""
    echo "📦 Comandos disponibles:"
    echo "  pytest                    # Ejecutar tests"
    echo "  pre-commit run --all-files # Ejecutar linters/formatters"
    echo "  python -m tools.run_stream # Ejecutar herramientas"
else
    echo "❌ Error: conda no encontrado en PATH"
    exit 1
fi
