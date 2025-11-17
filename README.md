# cripto_bot

Bot de trading de criptomonedas con micro-velas (tick bars, volume bars, dollar bars, imbalance bars).

## 🚀 Inicio Rápido

### Activar Entorno

El proyecto usa el entorno conda `cripto_bot`. Para activarlo y configurar PYTHONPATH automáticamente:

```bash
source activate.sh
```

O manualmente:

```bash
conda activate cripto_bot
export PYTHONPATH=$(pwd)/src
```

### Instalar Dependencias

```bash
pip install -r requirements.txt
pip install pre-commit
pre-commit install
```

### Ejecutar Tests

```bash
pytest                    # Ejecutar todos los tests
pytest -v                 # Modo verbose
pytest tests/test_*.py    # Ejecutar tests específicos
```

### Ejecutar Pre-commit

```bash
pre-commit run --all-files  # Ejecutar todos los hooks (ruff, black, mypy)
```

## 📝 Estructura del Proyecto

```
cripto_bot/
├── src/                      # Código fuente principal
│   ├── bars/                 # Builders de micro-velas
│   ├── brokers/              # Interfaz con exchanges
│   ├── core/                 # Motor de trading y lógica central
│   ├── strategies/           # Estrategias de trading
│   ├── data/                 # Feeds de datos y validación
│   └── tools/                # Herramientas (run_stream, etc.)
├── tests/                    # Tests unitarios e integración
├── tools/                    # Scripts de utilidad
└── data/                     # Datos de mercado
```

## 🔧 Configuración de Imports

El proyecto usa imports **sin el prefijo `src.`**. Esto requiere que `PYTHONPATH` apunte a `src/`:

```python
# ✅ Correcto
from bars.base import Trade
from core.broker import Broker
from strategies.momentum import MomentumStrategy

# ❌ Incorrecto
from src.bars.base import Trade
from src.core.broker import Broker
```

Nota sobre running tools
------------------------

Algunos scripts bajo `src/tools` esperan ser ejecutados con la carpeta `src` en
el Python import path. Para ello, usa uno de estos métodos:

### Método 1: Activar entorno (Recomendado)
```bash
source activate.sh
python -m tools.run_stream --symbol BTCUSDT --builder volume_qty --out data/bars_live/out.csv
```

### Método 2: PYTHONPATH explícito
```bash
PYTHONPATH=$(pwd)/src python -m tools.run_stream --symbol BTCUSDT --builder volume_qty --out data/bars_live/out.csv
```

## 🧪 Testing

El proyecto incluye:
- **Tests unitarios**: Validan componentes individuales
- **Tests de integración**: Validan flujos completos
- **Tests de imports**: Aseguran que imports normalizados funcionan

```bash
pytest tests/test_imports.py      # Tests de imports
pytest tests/test_builders.py     # Tests de builders
pytest tests/test_integration.py  # Tests de integración
```

## 🎨 Code Quality

El proyecto usa:
- **ruff**: Linter rápido (PEP8, pyflakes, isort, etc.)
- **black**: Formatter automático (line-length=120)
- **mypy**: Type checker estático
- **pre-commit**: Hooks automáticos antes de commit

Estos se ejecutan automáticamente con `pre-commit` o manualmente con:

```bash
ruff check --fix .
black .
mypy src/
```

## 📚 Herramientas Disponibles

### run_stream.py
Ingesta trades vía WebSocket y construye micro-barras en tiempo real:

```bash
python -m tools.run_stream \
  --symbol BTCUSDT \
  --builder volume_qty \
  --qty-limit 0.25 \
  --out data/bars_live/out.csv \
  --max-trades 10000
```

Builders disponibles:
- `tick_count` (--count)
- `volume_qty` (--qty-limit)
- `dollar` (--dollar-limit)
- `imbalance` (--alpha)

### run_mem_loop.py
Loop en memoria para validar estabilidad de builders con ticks sintéticos.

### inspect_last.py
Inspección rápida de archivos de micro-barras con métricas de calidad.

### run_live.py (desde CSV, con reporting enriquecido)
Runner sencillo que reproduce un flujo "live-like" a partir de un CSV (por reproducibilidad) y guarda salidas para análisis:

- equity.csv: t, price, qty, cash, equity por barra
- trades.csv: enriquecido con costes estimados vs reales (fee/slippage)
- decisions.csv: decisiones ejecutadas (t, price, side, qty, reason)
- summary.json: equity inicial/final, retorno total y número de barras
- manifest.json: metadatos del run (estrategia, params, símbolo, costes)
- quality.json: duración del run y barras/seg

Ejemplo:

```bash
PYTHONPATH=$(pwd)/src python -m tools.run_live \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ) \
  --source csv --csv runs/quick_check/data.csv \
  --symbol BTCUSDT --fees-bps 2.5 --slip-bps 1.0 --cash 100
```

En macOS, para evitar que el portátil duerma con la tapa cerrada mientras corre un run nocturno (7h ~ 25200s):

```bash
caffeinate -dimsu -t 25200 -- python -m tools.run_live \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ) \
  --source csv --csv runs/quick_check/data.csv \
  --symbol BTCUSDT --fees-bps 2.5 --slip-bps 1.0 --cash 100
```

## 🌐 Entorno y Configuración

## 📈 Estrategias Disponibles

| Estrategia        | Archivo                | Propósito Breve                                                |
|-------------------|------------------------|----------------------------------------------------------------|
| `momentum`        | `strategies/momentum.py`      | Momentum con filtros (volatilidad, cooling, SL/TP)             |
| `vol_breakout`    | `strategies/vol_breakout.py`  | Ruptura de canal con ATR y gestión de riesgo                   |
| `vwap_reversion`  | `strategies/vwap_reversion.py`| Reversión a VWAP usando z-score y TP/SL                        |

### Parámetros `momentum`
```json
{
  "lookback_ticks": 20,
  "entry_threshold": 0.0015,
  "exit_threshold": 0.001,
  "qty_frac": 1.0,
  "order_notional": 5.0,
  "stop_loss_pct": 0.01,
  "take_profit_pct": 0.02,
  "cooldown_bars": 3,
  "min_volatility": 0.0001,
  "max_volatility": 0.025,
  "trend_confirmation": true,
  "allow_short": false
}
```

### Parámetros `vol_breakout`
Se pueden pasar por JSON o flags dedicados:
| Flag | Clave JSON | Descripción | Default interno |
|------|------------|-------------|-----------------|
| `--vb-lookback` | `lookback` | Tamaño del canal | 20 |
| `--vb-atr-period` | `atr_period` | Periodo ATR | 14 |
| `--vb-atr-mult` | `atr_mult` | Multiplicador ATR para ruptura | 0.5 |
| `--vb-stop-mult` | `stop_mult` | Multiplicador ATR para stop | 2.0 |
| `--vb-qty-frac` | `qty_frac` | Fracción de capital | 1.0 |
| `--vb-debug` | `debug` | Logs detallados | False |

> Nota: cada entrada usa `order_notional` (5 USD por defecto, configurable vía `--params '{"order_notional":10}'`). Por defecto `allow_short=false`, así que solo abre largos salvo que se indique lo contrario.

Ejemplo:
```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
  --symbol BTCUSDT --strategy vol_breakout \
  --vb-lookback 30 --vb-atr-period 10 --vb-atr-mult 0.7 --vb-stop-mult 1.8 --vb-qty-frac 0.25
```

### Parámetros `vwap_reversion`
Flags dedicados:
| Flag | Clave JSON | Descripción | Default interno |
|------|------------|-------------|-----------------|
| `--vr-vwap-window` | `vwap_window` | Ventana VWAP/Z | 50 |
| `--vr-z-entry` | `z_entry` | Umbral entrada | 1.5 |
| `--vr-z-exit` | `z_exit` | Umbral salida | 0.5 |
| `--vr-take-profit-pct` | `take_profit_pct` | Take profit | 0.006 |
| `--vr-stop-loss-pct` | `stop_loss_pct` | Stop loss | 0.004 |
| `--vr-qty-frac` | `qty_frac` | Fracción capital | 1.0 |
| `--vr-warmup` | `warmup` | Barras warmup | = vwap_window |

> Nota: el tamaño real por trade es `order_notional` (5 USD por defecto, ajusta con `--params '{"order_notional":8}'`). Si no quieres cortos, deja `allow_short=false` (valor por defecto).

Ejemplo:
```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
  --symbol BTCUSDT --strategy vwap_reversion \
  --vr-vwap-window 40 --vr-z-entry 1.2 --vr-z-exit 0.3 --vr-take-profit-pct 0.008 --vr-stop-loss-pct 0.005 --vr-qty-frac 0.6
```

### Combinar con `--params`
Si se pasa `--params` (JSON) y flags específicos, los flags sobrescriben claves del JSON.

### Bar Builder (Composite)
Recordatorio de flags de micro-velas:
```bash
--bar-tick-limit 100       # trades
--bar-qty-limit 5.0        # BTC acumulados
--bar-value-limit 50000    # Notional USDT
--bar-imbal-limit 10.0     # Imbalance (no implementado aún)
--bar-policy any|all       # Política de cierre
```

Ejemplo completo con estrategia y builder:
```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
  --symbol BTCUSDT --cash 100 --fees-bps 10.0 \
  --strategy momentum --params '{"lookback_ticks": 20, "entry_threshold": 0.0015, "exit_threshold": 0.001}' \
  --bar-tick-limit 100 --bar-value-limit 50000 --bar-policy any
```

Variables de entorno importantes (`.env`):
- `PYTHONPATH`: Debe apuntar a `src/`
- `USE_TESTNET`: True para testnet, False para mainnet
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`: Credenciales de Binance

## 🤝 Contribuir

1. Asegúrate de que `pre-commit` esté instalado: `pre-commit install`
2. Escribe tests para nuevas funcionalidades
3. Ejecuta `pre-commit run --all-files` antes de commit
4. Asegúrate de que `pytest` pase sin errores
