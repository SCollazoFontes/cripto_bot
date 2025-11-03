# tools/smoke_validate.sh
#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
DIR="${2:-data/bars_live}"
PATTERN="${3:-btcusdt_volume_qty_*.csv}"

if [[ -z "$FILE" ]]; then
  # 1) encuentra el último archivo y haz validación estricta por CLI
  python -m tools.inspect_last --dir "$DIR" --pattern "$PATTERN" --strict \
    --require t_open t_close open high low close
else
  # 2) valida un archivo concreto con el validador directo
  python -m src.data.validate --path "$FILE" --strict \
    --require t_open t_close open high low close
fi

echo "✅ smoke_validate OK"
