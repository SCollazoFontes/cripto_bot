"""Agregador de barras por tiempo para gráficos (event-driven puro).

Independiente del BarBuilder (micro-velas). Solo crea/actualiza una barra
cuando ocurre un trade. No rellena huecos temporales a menos que se active
`gap_fill`. Cada intervalo sin trades se ignora (no se escribe barra
"sintética") para reflejar actividad real del mercado en testnet.
"""

import csv
from pathlib import Path


class TimeBarAggregator:
    """Agrega trades en barras de tiempo fijo (1s, 5s, 10s, 30s, 1m, 5m, 1H).

    Genera archivos CSV separados por timeframe para el dashboard:
    - chart_1s.csv
    - chart_5s.csv
    - chart_10s.csv
    - chart_30s.csv
    - chart_1m.csv
    - chart_5m.csv
    - chart_1h.csv

    Cada archivo tiene columnas: timestamp, open, high, low, close, volume, dollar_value.
    """

    def __init__(self, run_dir: Path, gap_fill: bool = False):
        """Inicializa el agregador de barras por tiempo (solo intervalos con trades).

        Args:
            run_dir: directorio donde se guardarán los archivos chart_<tf>.csv
        """
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Definir timeframes en segundos
        self.timeframes = {
            "1s": 1,
            "5s": 5,
            "10s": 10,
            "30s": 30,
            "1m": 60,
            "5m": 300,
            "1h": 3600,
        }

        # Estado actual de cada timeframe: {nombre: {"ts_start": int, "open": float, ...}}
        self.current_bars: dict[str, dict[str, float] | None] = {}
        self.last_price = 0.0
        self.gap_fill = gap_fill  # si True, rellena intervalos vacíos con barras planas (desactivado por defecto)

        # Inicializar archivos CSV
        for tf_name in self.timeframes:
            csv_path = self.run_dir / f"chart_{tf_name}.csv"
            if not csv_path.exists():
                with csv_path.open("w", newline="") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=[
                            "timestamp",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "dollar_value",
                            "volume_usdt",
                        ],
                    )
                    writer.writeheader()

            # Estado inicial vacío
            self.current_bars[tf_name] = None

    def update(self, timestamp: float, price: float, qty: float) -> None:
        """Procesa un trade y actualiza todas las barras de tiempo.

        Args:
            timestamp: timestamp del trade en segundos (float)
            price: precio del trade
            qty: cantidad (volumen base) del trade
        """
        self.last_price = price
        ts_sec = int(timestamp)

        for tf_name, interval_sec in self.timeframes.items():
            # Calcular el inicio del intervalo actual
            bar_start = (ts_sec // interval_sec) * interval_sec

            # Si no hay barra activa o cambió de intervalo, cerrar la anterior y crear nueva
            current_bar = self.current_bars[tf_name]

            if current_bar is None:
                # Primera barra
                self.current_bars[tf_name] = {
                    "ts_start": bar_start,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": qty,
                    "dollar_value": price * qty,
                }
            elif current_bar["ts_start"] == bar_start:
                # Mismo intervalo: actualizar OHLCV
                current_bar["high"] = max(current_bar["high"], price)
                current_bar["low"] = min(current_bar["low"], price)
                current_bar["close"] = price
                current_bar["volume"] += qty
                current_bar["dollar_value"] += price * qty
            else:
                # Cambió de intervalo: volcar la barra anterior
                self._flush_bar(tf_name)

                if self.gap_fill:
                    # Rellenar huecos (intervalos sin trades) con barras planas
                    last_start = int(current_bar["ts_start"])
                    gap_start = last_start + interval_sec
                    while gap_start < bar_start:
                        self._write_flat_bar(tf_name, gap_start)
                        gap_start += interval_sec

                # Iniciar nueva barra con el trade actual (solo cuando hay trade real)
                self.current_bars[tf_name] = {
                    "ts_start": bar_start,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": qty,
                    "dollar_value": price * qty,
                }

    def _flush_bar(self, tf_name: str) -> None:
        """Vuelca la barra actual de un timeframe al CSV."""
        bar = self.current_bars[tf_name]
        if bar is None:
            return

        csv_path = self.run_dir / f"chart_{tf_name}.csv"
        try:
            with csv_path.open("a", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "dollar_value",
                        "volume_usdt",
                    ],
                )
                writer.writerow(
                    {
                        "timestamp": bar["ts_start"],
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                        "dollar_value": bar["dollar_value"],
                        "volume_usdt": bar["dollar_value"],  # volume_usdt = dollar_value
                    }
                )
        except Exception:
            pass

    def _write_flat_bar(self, tf_name: str, ts_start: int) -> None:
        """Escribe una barra plana (sin volumen) para rellenar huecos de tiempo."""
        csv_path = self.run_dir / f"chart_{tf_name}.csv"
        try:
            with csv_path.open("a", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "dollar_value",
                        "volume_usdt",
                    ],
                )
                writer.writerow(
                    {
                        "timestamp": ts_start,
                        "open": self.last_price,
                        "high": self.last_price,
                        "low": self.last_price,
                        "close": self.last_price,
                        "volume": 0.0,
                        "dollar_value": 0.0,
                        "volume_usdt": 0.0,
                    }
                )
        except Exception:
            pass

    def finalize(self) -> None:
        """Cierra todas las barras activas al finalizar la sesión."""
        for tf_name in self.timeframes:
            if self.current_bars[tf_name] is not None:
                self._flush_bar(tf_name)
