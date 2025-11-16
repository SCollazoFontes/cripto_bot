# 🔧 PROBLEMA IDENTIFICADO: Barras con 1 solo trade

## TL;DR - Solución Rápida

**Objetivo:** Barras de ~1 segundo para trading de alta frecuencia

**MAINNET Paper Trading (100-200 trades/seg) - RECOMENDADO:**
```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_mainnet_1sec \
  --symbol BTCUSDT \
  --duration 180 \
  --cash 10000 \
  --fees-bps 10 \
  --slip-bps 5 \
  --strategy momentum \
  --bar-tick-limit 100 \
  --dashboard yes
```

**Regla de oro:** Para barras con control temporal, usa **SOLO** `--bar-tick-limit` y **NO** uses `value_limit` ni `qty_limit`.

---

## El Problema

Las barras se están cerrando después de **UN SOLO TRADE** porque los umbrales están mal configurados.

### Análisis de los parámetros actuales:

```bash
--bar-tick-limit 5          # OK: 5 trades por barra
--bar-qty-limit 0.001       # ❌ PROBLEMA: ~$95 USD (MUY BAJO)
--bar-value-limit 500       # ❌ PROBLEMA CRÍTICO: $500 (trades individuales valen ~$1000)
--bar-imbal-limit 3         # ❌ PROBLEMA: Se alcanza con 3 trades consecutivos
--bar-imbal-mode tick       # OK pero agresivo
--bar-policy any            # ❌ PROBLEMA: Cierra con CUALQUIER umbral
```

### ¿Por qué falla?

Con `--bar-policy any` (OR lógico), la barra se cierra cuando **CUALQUIER** umbral se alcanza.
Los trades individuales en Binance para BTCUSDT suelen tener valores de $500-$2000, por lo que:

1. **Un solo trade** ya supera `bar-value-limit=500` → **BARRA CERRADA** ❌
2. Aunque hayas configurado `tick-limit=5`, nunca se alcanza porque `value-limit` se cumple primero
3. `qty-limit=0.001` también se cumple rápidamente (~1-2 trades)

## Soluciones Recomendadas

### Opción 1: Aumentar umbrales (RECOMENDADO)

Para barras de 5-10 segundos con volumen razonable en MAINNET (paper trading):

```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_fixed \
  --symbol BTCUSDT \
  --duration 180 \
  --cash 10000 \
  --fees-bps 10 \
  --slip-bps 5 \
  --strategy momentum \
  --bar-tick-limit 50 \              # 50 trades por barra (antes: 5)
  --bar-qty-limit 0.05 \             # 0.05 BTC = ~$4,500 (antes: 0.001)
  --bar-value-limit 10000 \          # $10,000 negociados (antes: 500)
  --bar-imbal-limit 30 \             # Desequilibrio de 30 (antes: 3)
  --bar-imbal-mode tick \
  --bar-policy any \                 # Cualquier umbral (más barras)
  --dashboard yes
```

### Opción 2: Solo usar tick-limit (MÁS SIMPLE)

Si quieres barras predecibles basadas solo en número de trades en MAINNET (paper trading):

```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_tick_only \
  --symbol BTCUSDT \
  --duration 180 \
  --cash 10000 \
  --fees-bps 10 \
  --slip-bps 5 \
  --strategy momentum \
  --bar-tick-limit 100 \             # Solo esto
  --dashboard yes
  # NO especificar otros umbrales
```

### Opción 3: Política ALL (todas las condiciones)

Para barras más "densas" que cumplan TODOS los umbrales en MAINNET (paper trading):

```bash
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_all_policy \
  --symbol BTCUSDT \
  --duration 180 \
  --cash 10000 \
  --fees-bps 10 \
  --slip-bps 5 \
  --strategy momentum \
  --bar-tick-limit 50 \
  --bar-qty-limit 0.05 \
  --bar-value-limit 10000 \
  --bar-policy all \                 # ← Cambio clave: requiere TODOS los umbrales
  --dashboard yes
```

## Valores de Referencia para BTCUSDT

| Métrica | Trade Individual | Recomendado por Barra | Mínimo Seguro |
|---------|-----------------|----------------------|---------------|
| Trades | 1 | 50-200 | 20 |
| Cantidad (BTC) | 0.001-0.02 | 0.05-0.1 | 0.02 |
| Valor (USD) | $500-$2000 | $10,000-$50,000 | $5,000 |
| Imbalance (tick) | ±1 | ±20-50 | ±10 |

## Comandos Recomendados para Trading de Alta Frecuencia

### MAINNET Paper Trading (barras de ~1 segundo) - RECOMENDADO

MAINNET tiene **alto volumen**: ~100-200 trades/segundo (datos reales, trades simulados)

```bash
# Comando directo:
python -m tools.live.run_binance \
  --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_mainnet_1sec \
  --symbol BTCUSDT \
  --duration 180 \
  --cash 10000 \
  --fees-bps 10 \
  --slip-bps 5 \
  --strategy momentum \
  --bar-tick-limit 100 \
  --dashboard yes
```

### Analizar frecuencia antes de configurar

```bash
# Para MAINNET (recomendado)
python tools/analizar_frecuencia_trades.py --duration 30

# Para testnet (solo si necesitas probar conectividad)
python tools/analizar_frecuencia_trades.py --testnet --duration 30
```

Esto te dirá exactamente cuántos trades/segundo hay y el tick_limit óptimo.
