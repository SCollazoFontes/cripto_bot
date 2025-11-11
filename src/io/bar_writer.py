# scr/io/bar_writer.py
"""
Persistencia ligera y no bloqueante de micro-velas.

Este módulo provee `AsyncBarWriter`, un escritor en segundo plano (hilo dedicado)
para volcar barras cerradas a disco sin frenar el loop de construcción.

✔️ Por qué existe:
- Reproducibilidad: volver a usar EXACTAMENTE las mismas barras.
- Sesiones largas: descarga incremental para no llenar RAM.
- Comparabilidad: distintos motores/estrategias sobre idénticas micro-velas.

📦 Salida soportada: CSV (siempre), JSONL (siempre), Parquet (opcional si hay
`pyarrow`).

🧩 Integración (ejemplo en `tools.run_stream`):

    from io.bar_writer import AsyncBarWriter

    writer = AsyncBarWriter(symbol=symbol, rule=rule, limit=limit,
                            out_dir="data/bars_live", fmt="csv",
                            flush_every_secs=2.0, flush_every_n=500)
    writer.start()

    # dentro del loop cuando una barra se cierra:
    if bar:
        writer.write(bar)  # encola sin bloquear el stream

    # al terminar
    writer.close()  # vacía cola, hace flush, cierra archivo y el hilo

Formato de columnas (depende de atributos presentes en la barra):
- t_open, t_close: epoch ms (si existen o se derivan de start/end)
- open, high, low, close
- volume, dollar_value (si existen o se derivan)
- trade_count
- duration_ms, gap_ms (diagnóstico temporal intra/inter-barra)
- extras: session_id, symbol, rule, limit, bar_index, target, overshoot, overshoot_pct
"""

from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import IO, Any

logger = logging.getLogger(__name__)

try:  # parquet opcional
    import pyarrow as pa
    import pyarrow.parquet as pq

    _HAVE_PARQUET = True
except Exception:  # pragma: no cover - si no está instalado
    _HAVE_PARQUET = False


# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sanitize_rule(rule: str) -> str:
    return rule.strip().lower().replace(" ", "_")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _to_epoch_ms(dt: Any) -> int | None:
    """Convierte un datetime/seg/ms a epoch ms (int)."""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        # si es pequeño (segundos) pásalo a ms; si es grande ya está en ms
        return int(dt if dt > 2_000_000_000 else dt * 1000)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    return None


def _guess_bar_dict(bar: Any) -> dict[str, Any]:
    """Convierte una barra arbitraria en `dict` serializable.

    Soporta:
    - dataclasses (asdict)
    - dicts
    - objetos con atributos estándar: open/high/low/close/volume/trade_count
      y tiempos: t_open/t_close o open_time/close_time/start_time/end_time
    """
    if is_dataclass(bar) and not isinstance(bar, type):
        d = asdict(bar)
    elif isinstance(bar, dict):
        d = dict(bar)
    else:
        fields = (
            "t_open",
            "t_close",
            "open_time",
            "close_time",
            "start_time",
            "end_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "dollar_value",
            "trade_count",
        )
        d = {k: getattr(bar, k, None) for k in fields}

    # Normaliza timestamps a t_open/t_close (epoch ms)
    if d.get("t_open") is None:
        t = d.get("open_time") or d.get("start_time")
        d["t_open"] = _to_epoch_ms(t)
    if d.get("t_close") is None:
        t = d.get("close_time") or d.get("end_time")
        d["t_close"] = _to_epoch_ms(t)

    ordered: dict[str, Any] = {
        "t_open": d.get("t_open"),
        "t_close": d.get("t_close"),
        "open": d.get("open"),
        "high": d.get("high"),
        "low": d.get("low"),
        "close": d.get("close"),
        "volume": d.get("volume"),
        "dollar_value": d.get("dollar_value"),
        "trade_count": d.get("trade_count"),
        "start_time": d.get("start_time"),
        "end_time": d.get("end_time"),
    }
    # Resto de campos
    for k, v in d.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


# --------------------------------------------------------------------------------------
# Escritor asíncrono (hilo dedicado)
# --------------------------------------------------------------------------------------


class AsyncBarWriter:
    """Escritor no bloqueante de barras a disco mediante un hilo dedicado.

    Parámetros
    ----------
    symbol : str
        Símbolo (p.ej. "btcusdt"). Se guarda en metadatos por fila.
    rule : str
        Regla de construcción ("tick", "volume_qty", "dollar", "imbalance", ...).
    limit : float
        Límite principal de la regla (ticks, qty, dólares, etc.).
    out_dir : str | Path
        Carpeta base donde escribir los ficheros de sesión. (default: "data/bars_live")
    fmt : {"csv", "jsonl", "parquet"}
        Formato de salida. Parquet requiere `pyarrow`. (default: "csv")
    flush_every_secs : float
        Frecuencia mínima de flush a disco. (default: 2.0)
    flush_every_n : int
        Nº mínimo de barras acumuladas para provocar flush. (default: 500)
    session_name : Optional[str]
        Nombre de sesión (si no se provee se autogenera).
    """

    def __init__(
        self,
        *,
        symbol: str,
        rule: str,
        limit: float,
        out_dir: str | Path = "data/bars_live",
        fmt: str = "csv",
        flush_every_secs: float = 2.0,
        flush_every_n: int = 500,
        session_name: str | None = None,
    ) -> None:
        self.symbol = symbol.lower()
        self.rule = _sanitize_rule(rule)
        self.limit = limit
        self.out_dir = Path(out_dir)
        self.fmt = fmt.lower()
        self.flush_every_secs = flush_every_secs
        self.flush_every_n = flush_every_n

        if self.fmt not in {"csv", "jsonl", "parquet"}:
            raise ValueError("fmt debe ser 'csv', 'jsonl' o 'parquet'")
        if self.fmt == "parquet" and not _HAVE_PARQUET:
            raise RuntimeError("Parquet requiere pyarrow. Instálalo o usa csv/jsonl.")

        _ensure_dir(self.out_dir)
        start_stamp = _now_utc_iso()
        self.session_name = (
            session_name or f"{self.symbol}_{self.rule}_{self._limit_tag()}_{start_stamp}"
        )
        self.file_path = self.out_dir / f"{self.session_name}.{self._ext()}"

        # Estado de hilo/cola
        self._q: Queue[dict[str, Any]] = Queue(maxsize=100_000)
        self._stop = threading.Event()
        self._thr: threading.Thread | None = None

        # Buffers y timers internos (del hilo escritor)
        self._buffer: list[dict[str, Any]] = []
        self._last_flush_ts: float = time.monotonic()
        self._bar_index: int = 0  # contador interno por sesión
        self._prev_end_ms: int | None = None  # para gap_ms

        # Recursos de salida tipados
        self._csv_fp: IO[str] | None = None
        self._csv_writer: csv.DictWriter | None = None
        self._parquet_rows: list[dict[str, Any]] = []

        logger.info(
            "AsyncBarWriter sesión=%s destino=%s fmt=%s",
            self.session_name,
            self.file_path,
            self.fmt,
        )

    # ----------------------------- API pública -------------------------------------

    def start(self) -> None:
        if self._thr is not None:
            return
        self._thr = threading.Thread(target=self._run_loop, name="BarWriterThread", daemon=True)
        self._thr.start()

    def write(self, bar: Any) -> None:
        """Encola una barra. Retorna inmediatamente (no bloquea el stream)."""
        try:
            row = _guess_bar_dict(bar)

            # --------- enriquecer row con diagnósticos y metadatos ----------
            t_open = row.get("t_open")
            t_close = row.get("t_close")
            row["t_open"] = t_open
            row["t_close"] = t_close
            row["duration_ms"] = (
                (t_close - t_open) if (t_open is not None and t_close is not None) else None
            )
            row["gap_ms"] = (
                (t_open - self._prev_end_ms)
                if (t_open is not None and self._prev_end_ms is not None)
                else None
            )
            if t_close is not None:
                self._prev_end_ms = t_close

            # --- Completar dollar_value si falta (proxy close*volume) ---
            if (
                row.get("dollar_value") is None
                and row.get("close") is not None
                and row.get("volume") is not None
            ):
                try:
                    row["dollar_value"] = float(row["close"]) * float(row["volume"])
                except Exception:
                    # preferimos no romper la escritura por un tipo raro
                    pass

            # --- target / overshoot por tipo de regla ---
            try:
                if self.rule in ("volume_qty", "volume"):
                    row["target"] = float(self.limit)
                    vol = float(row["volume"]) if row.get("volume") is not None else None
                    row["overshoot"] = (vol - row["target"]) if vol is not None else None
                    row["overshoot_pct"] = (
                        (row["overshoot"] / row["target"])
                        if (row.get("overshoot") is not None and row["target"])
                        else None
                    )
                elif self.rule in ("dollar", "value"):
                    row["target"] = float(self.limit)
                    dval = (
                        float(row["dollar_value"]) if row.get("dollar_value") is not None else None
                    )
                    row["overshoot"] = (dval - row["target"]) if dval is not None else None
                    row["overshoot_pct"] = (
                        (row["overshoot"] / row["target"])
                        if (row.get("overshoot") is not None and row["target"])
                        else None
                    )
                elif self.rule in ("tick", "ticks", "tick_count"):
                    row["target"] = int(self.limit)
                    tc = int(row["trade_count"]) if row.get("trade_count") is not None else None
                    row["overshoot"] = (tc - row["target"]) if tc is not None else None
                    row["overshoot_pct"] = (
                        (row["overshoot"] / row["target"])
                        if (row.get("overshoot") is not None and row["target"])
                        else None
                    )
            except Exception:
                # no interrumpir el flujo por un fallo de diagnóstico secundario
                pass

            # metadatos de sesión
            row["symbol"] = self.symbol
            row["rule"] = self.rule
            row["limit"] = self.limit
            row["session_id"] = self.session_name
            row["bar_index"] = self._bar_index
            self._bar_index += 1

            self._q.put_nowait(row)
        except Exception as e:  # pragma: no cover
            logger.exception("Fallo en write(): %s", e)

    def close(self) -> None:
        """Detiene el hilo, vacía la cola y cierra el archivo."""
        self._stop.set()
        if self._thr is not None:
            self._thr.join(timeout=10)
            self._thr = None
        # Asegura flush final
        try:
            self._flush(force=True)
        finally:
            self._close_outputs()
            logger.info("AsyncBarWriter cerrado: %s", self.file_path)

    # ----------------------------- Internos ----------------------------------------

    def _run_loop(self) -> None:
        self._open_outputs_if_needed()
        while not self._stop.is_set():
            try:
                # Consume en lotes para eficiencia
                batch: list[dict[str, Any]] = []
                # Espera con timeout para poder comprobar el evento de parada
                item = self._q.get(timeout=0.2)
                batch.append(item)
                # Drena rápido lo que haya disponible ahora mismo
                while True:
                    try:
                        batch.append(self._q.get_nowait())
                    except Empty:
                        break
                self._buffer.extend(batch)
            except Empty:
                pass
            except Exception as e:  # pragma: no cover
                logger.exception("Loop escritor error: %s", e)

            # Política de flush
            now = time.monotonic()
            should_by_time = (now - self._last_flush_ts) >= self.flush_every_secs
            should_by_size = len(self._buffer) >= self.flush_every_n
            if should_by_time or should_by_size:
                self._flush()

        # Al salir, vaciar todo lo pendiente
        while True:
            try:
                self._buffer.append(self._q.get_nowait())
            except Empty:
                break
        self._flush(force=True)

    # --- Salidas por formato ---

    def _open_outputs_if_needed(self) -> None:
        if self.fmt == "csv":
            # Abre en modo append y escribe cabecera si el archivo está vacío
            new_file = not self.file_path.exists()
            self._csv_fp = open(self.file_path, "a", newline="", encoding="utf-8")
            self._csv_writer = None  # se inicializa en el primer flush cuando haya columnas
            if new_file:
                # Cabecera se escribe en _flush cuando conozcamos el orden exacto de columnas
                pass
        elif self.fmt == "jsonl":
            # Nada que mantener abierto, se escribe por líneas en _flush
            pass
        elif self.fmt == "parquet":
            # acumulamos filas y volcamos en bloques
            self._parquet_rows = []

    def _close_outputs(self) -> None:
        try:
            if self._csv_fp is not None:
                self._csv_fp.close()
        except Exception:  # pragma: no cover
            pass
        self._csv_fp = None
        self._csv_writer = None
        self._parquet_rows = []

    def _flush(self, *, force: bool = False) -> None:
        if not self._buffer and not force:
            self._last_flush_ts = time.monotonic()
            return
        if not self._buffer and force:
            self._last_flush_ts = time.monotonic()
            return

        n = len(self._buffer)
        try:
            if self.fmt == "csv":
                self._flush_csv(self._buffer)
            elif self.fmt == "jsonl":
                self._flush_jsonl(self._buffer)
            elif self.fmt == "parquet":
                self._flush_parquet(self._buffer)
        finally:
            self._buffer.clear()
            self._last_flush_ts = time.monotonic()
            logger.info("bar_writer: wrote %d rows → %s", n, self.file_path)

    # --- Implementaciones por formato ---

    def _flush_csv(self, rows: list[dict[str, Any]]) -> None:
        assert self._csv_fp is not None
        # Determina orden de columnas fijo basado en la primera fila del batch
        first = rows[0]
        fieldnames = list(first.keys())
        if self._csv_writer is None:
            # Si el archivo está vacío, escribimos cabecera
            if self.file_path.stat().st_size == 0:
                tmp_writer = csv.DictWriter(self._csv_fp, fieldnames=fieldnames)
                tmp_writer.writeheader()
                self._csv_fp.flush()
            self._csv_writer = csv.DictWriter(self._csv_fp, fieldnames=fieldnames)

        assert self._csv_writer is not None
        self._csv_writer.writerows(rows)
        self._csv_fp.flush()
        os.fsync(self._csv_fp.fileno())  # mayor durabilidad ante cortes

    def _flush_jsonl(self, rows: list[dict[str, Any]]) -> None:
        # Abrimos/close por flush para simplicidad (rendimiento ok a tamaños moderados)
        with open(self.file_path, "a", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r, ensure_ascii=False) + "\n")
            fp.flush()
            os.fsync(fp.fileno())

    def _flush_parquet(self, rows: list[dict[str, Any]]) -> None:
        if not _HAVE_PARQUET:
            raise RuntimeError("pyarrow no disponible")
        # Convertimos a tabla y anexamos al archivo (escritura por lotes)
        table = pa.Table.from_pylist(rows)
        if not self.file_path.exists():
            pq.write_table(table, self.file_path)
        else:
            # Append sencillo: reabrimos y concatenamos
            # (para ultra performance usar writer incremental)
            existing = pq.read_table(self.file_path)
            new_table = pa.concat_tables([existing, table])
            pq.write_table(new_table, self.file_path)

    # ----------------------------------------------------------------------------------
    # Utilidades varias
    # ----------------------------------------------------------------------------------

    def _ext(self) -> str:
        return {"csv": "csv", "jsonl": "jsonl", "parquet": "parquet"}[self.fmt]

    def _limit_tag(self) -> str:
        # etiqueta compacta para el nombre de sesión (sin puntos en floats)
        if isinstance(self.limit, float):
            return f"{self.limit:g}".replace(".", "_")
        return str(self.limit)


__all__ = ["AsyncBarWriter"]
