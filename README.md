# 🤖 cripto_bot

Bot de trading de criptomonedas con **micro-velas adaptativas** y **estrategias dinámicas**.

Construye barras a partir de trades: tick bars, volume bars, dollar bars, imbalance bars.  
Ejecuta estrategias en vivo con dashboard en tiempo real.

---

## 🚀 Inicio Rápido

### Activar Entorno

```bash
source activate.sh
# O manualmente:
conda activate cripto_bot
export PYTHONPATH=$(pwd)/src
```

### Instalar Dependencias

```bash
pip install -r requirements.txt
pre-commit install  # (opcional)
```

### Ejecutar Tests

```bash
pytest                    # Todos los tests
pytest -v                 # Verbose
pytest tests/test_*.py    # Tests específicos
```

---

## 🔬 Fase Actual: Optimización de Bar Builders

Estamos optimizando la configuración de micro-velas (tick_limit, value_limit, policy) para encontrar cuál funciona mejor con Momentum.

---

## 📁 Estructura del Proyecto

```
cripto_bot/
├── src/                          # Código fuente principal
│   ├── bars/                     # Builders de micro-velas
│   │   ├── base.py              # Trade, Bar (tipos base)
│   │   ├── registry.py          # Registro de builders
│   │   ├── aggregators/         # Aggregators (tick, volume, dollar, imbalance)
│   │   ├── builders/            # Builders específicos
│   │   └── utils/               # Utilidades de barras
│   │
│   ├── brokers/                 # Interfaz con exchanges
│   │   ├── base.py              # Broker base
│   │   └── binance_paper.py     # Paper trading (Binance)
│   │
│   ├── core/                    # Motor de trading
│   │   ├── execution/           # Ejecución de órdenes
│   │   ├── metrics/             # Cálculo de métricas
│   │   ├── monitoring/          # Monitoring y alertas
│   │   ├── config_loader.py     # Carga de configuración
│   │   ├── decisions_log.py     # Log de decisiones
│   │   ├── io.py                # I/O (CSV, JSON)
│   │   ├── logger_config.py     # Configuración de logs
│   │   └── types.py             # Tipos compartidos
│   │
│   ├── strategies/              # Estrategias de trading
│   │   ├── base.py              # Strategy base
│   │   └── momentum.py           # ⭐ Momentum strategy (ACTIVA + ADAPTATIVA)
│   │
│   ├── data/                    # Feeds de datos
│   │   ├── bars.py              # Parser de barras
│   │   ├── validate.py          # Validación de datos
│   │   └── feeds/               # Feeds de datos (Binance, CSV)
│   │
│   └── tools/                   # Herramientas internas
│       └── run_stream.py        # Streaming en vivo
│
├── tools/                       # Scripts de utilidad
│   ├── data/                    # Gestión de datos
│   │   ├── update_master_dataset.py    # Descargar/actualizar trades de Binance
│   │   ├── make_bars.py                # Generar barras desde trades
│   │   ├── validate_bars.py            # Validar barras
│   │   ├── inspect_last.py             # Inspeccionar últimos datos
│   │   └── capture_testnet_ticks.py    # Captura testnet (opcional)
│   │
│   ├── optimize/                # Optimización y backtesting
│   │   ├── momentum.py                 # Evaluador de Momentum
│   │   ├── builder_configs.py          # Catálogo de builders
│   │   ├── datasets.py                 # Windowing de datos
│   │   ├── optimizers.py               # Grid/Random/Bayes search
│   │   ├── runner_v2.py                # Orchestrator de optimización
│   │   └── run_momentum.py             # Runner simple para Momentum
│   │
│   ├── analysis/                # Análisis y validación
│   │   ├── quick_run_momentum.py       # ⭐ Ejecución rápida de Momentum
│   │   └── walkforward_momentum.py     # ⭐ Validación Walk-Forward (5 folds)
│   │
│   ├── live/                    # Trading en vivo
│   │   ├── executor.py                 # Ejecutor de órdenes
│   │   └── output_writers.py           # Writers de output
│   │
│   ├── visual/                  # Dashboard en tiempo real
│   │   ├── layout.py                   # Layout principal
│   │   ├── chart_ohlc.py               # Gráfico OHLC
│   │   ├── ohlc_candles.py             # Rendering de candles
│   │   ├── kill_switch.py              # Kill switch UI
│   │   └── components/                 # Componentes del dashboard
│   │       ├── decision_panel.py
│   │       ├── kpis_panel.py
│   │       ├── metrics_header.py
│   │       ├── position_panel.py
│   │       ├── signal_panel.py
│   │       └── timeframe.py
│   │
│   └── run_live_binance.py      # Entry point para live trading
│
├── tests/                       # Tests unitarios e integración
├── data/                        # Datos
│   ├── datasets/                # CSV maestros
│   │   └── BTCUSDT_master.csv   # Trade agregados Binance
│   └── bars_live/               # Barras en tiempo real
├── runs/                        # Resultados de backtests
└── requirements.txt             # Dependencias
```

---

## 🎯 Estrategias

### Momentum Strategy ⭐ (ACTIVA)

**Ubicación**: `src/strategies/momentum.py`

**Estado**: ✅ Producción (con adaptabilidad dinámica)

**Características**:
- Entrada: Momentum > threshold + confirmación de tendencia
- Salidas:
  - Stop Loss dinámico (adapta según volatilidad)
  - Take Profit dinámico (adapta según volatilidad)
  - Reversal (cambio de momentum)
- Protecciones:
  - Min profit floor (30 bps para cubrir costes)
  - Cooldown dinámico (adapta según rentabilidad del trade anterior)
  - Entry threshold adaptativo (más selectivo en volatilidad alta)
  - Trend strength validation (opcional)

**Parámetros configurables**:
- `lookback_ticks`: Ventana para media móvil (default: 50)
- `entry_threshold`: Momentum mínimo (default: 0.0011)
- `stop_loss_pct`: SL % (default: 1.5%, dinámico si activado)
- `take_profit_pct`: TP % (default: 2.5%, dinámico si activado)
- `min_profit_bps`: Profit mínimo en bps (default: 60)
- `use_dynamic_sl/tp/entry/cooldown/min_profit`: Flags para activar adaptabilidad (todos False por defecto)
- `use_trend_strength`: Validación de fuerza de tendencia (default: False)

**Resultado reciente** (24 días, compact_60ticks):
- Retorno: +0.00144%
- Trades: 3
- Última barra cerrada: 15,546

---

## 🔧 Guía de Uso

### 1️⃣ Actualizar Base de Datos

Descarga/actualiza trades desde Binance:

```bash
python3 -m tools.data.update_master_dataset \
  --symbol BTCUSDT \
  --mode binance_trades \
  --max-days 365 \
  --out data/datasets/BTCUSDT_master.csv
```

**Opciones**:
- `--start "2025-12-01"`: Desde fecha específica
- `--max-days 30`: Solo últimos 30 días
- `--chunk-minutes 240`: Chunk size para downloads

---

### 2️⃣ Ejecutar Backtest Rápido (7 días)

```bash
python3 -m tools.analysis.quick_run_momentum \
  --builder compact_60ticks \
  --window 7d \
  --params '{
    "lookback_ticks":50,
    "entry_threshold":0.0011,
    "exit_threshold":0.00015,
    "min_profit_bps":60,
    "use_dynamic_sl":true,
    "use_dynamic_tp":true
  }'
```

Salida en: `runs/<timestamp>/`

---

### 3️⃣ Validación Walk-Forward (30 días en 5 folds)

```bash
python3 -m tools.analysis.walkforward_momentum \
  --builder compact_60ticks \
  --dataset data/datasets/BTCUSDT_master.csv
```

Evalúa parámetros en:
- 5 folds (6-7 días cada uno)
- Optimiza -> prueba sin data leakage
- Retorna agregado de todos los folds

---

### 4️⃣ Trading en Vivo (con Dashboard)

```bash
python3 tools/run_live_binance.py
```

Visualiza en tiempo real:
- Candles OHLC
- Señales de entrada/salida
- KPIs (retorno, trades, win rate)
- Posición actual
- Panel de decisiones

---

## 📊 Arquitectura de Barras

Soporta múltiples tipos de barras:

| Builder | Parámetros | Uso |
|---------|-----------|-----|
| `tick_bars` | `n_ticks` | Barras cada N trades |
| `volume_bars` | `volume_qty` | Barras cada N USD volumen |
| `dollar_bars` | `dollar_value` | Barras cada N USD notional |
| `imbalance_bars` | `imbalance_pct` | Barras según desbalance B/S |
| `hybrid_*` | Mix de anterior | Combinaciones de criterios |

**Recomendado**: `compact_60ticks` (60 ticks, policy="any")

---

## 🧪 Testing

```bash
# Todos los tests
pytest

# Tests específicos
pytest tests/test_imports.py
pytest tests/test_builders.py
pytest tests/test_momentum.py

# Con coverage
pytest --cov=src tests/
```

---

## ⚙️ Configuración de Python

Imports **sin prefijo `src/`**:

```python
# ✅ Correcto
from bars.base import Trade
from core.execution.costs import CostModel
from strategies.momentum import MomentumStrategy

# ❌ Incorrecto (no hagas esto)
from src.bars.base import Trade
```

Requiere: `export PYTHONPATH=$(pwd)/src`

O usa: `source activate.sh`

---

## 📈 Próximos Pasos

- [ ] Optimizar parámetros de Momentum con grid search
- [ ] Implementar más estrategias (RSI, Bollinger Bands, etc.)
- [ ] Integrar órdenes reales en Binance (modo producción)
- [ ] Backtesting paralelo con múltiples estrategias
- [ ] ML para predicción de reversiones

---

## 🐛 Troubleshooting

### "ModuleNotFoundError: No module named 'bars'"
```bash
export PYTHONPATH=$(pwd)/src
# O:
source activate.sh
```

### Tests fallan con import errors
```bash
pytest --no-header -rN  # Desactiva headers
```

### Memoria insuficiente en backtests largos
Reduce el tamaño del dataset:
```python
--max-days 7  # Solo últimos 7 días
```

---

## 📞 Info

- **Última actualización**: 8 diciembre 2025
- **Python**: 3.10+
- **Exchanges**: Binance (Spot, paper trading)
- **Estrategias activas**: Momentum (con adaptabilidad dinámica)
