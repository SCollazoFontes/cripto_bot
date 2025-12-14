# 🔧 Optimización V2: Menos Archivos, Mejor Organización

## 🎯 Problema Resuelto

**Antes (runner.py)**:
- Cada trial generaba su propia carpeta
- 956 trials = 956 carpetas con archivos duplicados
- Difícil de analizar resultados globalmente

**Ahora (runner_v2.py)**:
- 1 archivo consolidado por ventana (`all_trials.json`)
- Solo se guarda la carpeta del mejor trial (`best_trial/`)
- 20 trials × 8 ventanas = **8 archivos** en lugar de 160 carpetas

## 📊 Estructura de Salida

```
runs_opt/
  <builder_name>/              # ej: hybrid_100ticks_all
    <strategy>/                # ej: momentum
      <optimizer>/             # ej: grid, random, bayes
        <window_label>/        # ej: 1d, 3h, 7d
          all_trials.json      # TODOS los trials consolidados
          best_trial/          # Solo el mejor trial (opcional)
            opt_summary.json   # Formato compatible con consolidador
```

### Contenido de `all_trials.json`:

```json
{
  "strategy": "momentum",
  "optimizer": "grid",
  "window": {
    "label": "1d",
    "start_ts": 1733011200.0,
    "end_ts": 1733097600.0,
    "ts_unit": "s"
  },
  "total_trials": 20,
  "best_trial": {
    "params": {"lookback_ticks": 40, "entry_threshold": 0.0015},
    "score": 0.000679,
    "metrics": {"total_return": 0.000679, "trades": 4}
  },
  "trials": [
    {
      "trial_idx": 1,
      "params": {"lookback_ticks": 20, "entry_threshold": 0.001},
      "score": 0.000345,
      "metrics": {"total_return": 0.000345, "trades": 2}
    },
    ...
  ]
}
```

## 🚀 Uso

### Opción 1: Usar el runner V2 directamente

Modifica tus scripts de optimización para usar `runner_v2`:

```python
# Antes:
from tools.optimize.runner import OptimizationRunner

# Ahora:
from tools.optimize.runner_v2 import OptimizationRunner

# El resto del código es igual, pero ahora pasas builder_name:
runner = OptimizationRunner(target, config)
results = runner.run(builder_name="hybrid_100ticks_all")
```

### Opción 2: Configurar save_best_only

```python
config = OptimizationConfig(
    # ... otros parámetros ...
    save_best_only=True,   # Solo guarda el mejor (default)
    # save_best_only=False,  # Guarda todos los trials completos
)
```

## 📈 Consolidación de Resultados

Genera un CSV con TODOS los experimentos históricos:

```bash
source activate.sh
python -m tools.analysis.consolidate_optimization_results \
  --input runs_opt \
  --output runs_opt/consolidated_results.csv
```

El CSV incluye:
- Estrategia, optimizer, builder, ventana
- Score, return, trades, equity final
- Todos los parámetros (param_*)
- Ruta al run para debugging

## 📉 Comparación de Espacio

**Escenario: 10 builders × 8 ventanas × 20 trials = 1,600 trials**

| Método | Archivos | Carpetas | Espacio Estimado |
|--------|----------|----------|------------------|
| runner.py (viejo) | ~6,400 | 1,600 | ~25 MB |
| runner_v2.py (nuevo) | ~160 | 80 | ~2 MB |
| **Reducción** | **-97%** | **-95%** | **-92%** |

## 🔄 Migración desde V1

Si ya tienes resultados con el runner viejo, usa el consolidador:

```bash
# 1. Consolidar resultados existentes
python -m tools.analysis.consolidate_optimization_results \
  --input runs_opt \
  --output runs_opt/consolidated_results.csv

# 2. (Opcional) Archivar runs antiguos
mkdir runs_opt_archive
mv runs_opt/* runs_opt_archive/
mv runs_opt_archive/consolidated_results.csv runs_opt/

# 3. Empezar a usar runner_v2 para nuevas optimizaciones
```

## 💡 Ventajas

1. **Menos archivos**: 95% menos carpetas y archivos
2. **Análisis más fácil**: Todo en un JSON por ventana
3. **Compatible**: El consolidador sigue funcionando
4. **Flexible**: Puedes guardar todos los trials si quieres (save_best_only=False)
5. **Menos espacio**: ~92% menos espacio en disco

## 🛠️ Ejemplo Completo

```python
from tools.optimize.runner_v2 import OptimizationRunner, OptimizationConfig
from tools.optimize.momentum import build_momentum_target, BrokerParams
from tools.optimize.datasets import DatasetSpec
from tools.optimize.builder_configs import get_builder

# Setup
dataset = DatasetSpec(Path("data/datasets/BTCUSDT_master.csv"))
builder = get_builder("hybrid_100ticks_all")
broker_params = BrokerParams(fees_bps=10.0, slip_bps=5.0, starting_cash=100.0)

# Target
target = build_momentum_target(
    symbol="BTCUSDT",
    builder_cfg=builder.as_kwargs(),
    broker_params=broker_params,
    grid_mode=False,
    min_trades=5,
)

# Config
config = OptimizationConfig(
    dataset=dataset,
    windows=["1d", "3d", "7d"],
    optimizer_name="random",
    max_trials=50,
    save_best_only=True,  # Solo el mejor trial
)

# Run
runner = OptimizationRunner(target, config)
results = runner.run(builder_name=builder.name)
```

## 📝 Notas

- `runner.py` sigue disponible para compatibilidad
- `runner_v2.py` es el recomendado para nuevos experimentos
- Ambos son compatibles con el consolidador
- El formato de `opt_summary.json` es el mismo en ambos
