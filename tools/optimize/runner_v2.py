# tools/optimize/runner_v2.py
from __future__ import annotations

"""
Runner mejorado para optimización con menos archivos generados.

Cambios vs runner.py original:
    1. Genera UN SOLO archivo consolidado por ventana (all_trials.json)
    2. Opcionalmente guarda solo el mejor trial completo (best_trial/)
    3. Reduce drasticamente el número de archivos generados
    
Estructura de salida:
    runs_opt/
        <builder_name>/
            <strategy>/
                <optimizer>/
                    <window_label>/
                        all_trials.json      <- Todos los trials en un archivo
                        best_trial/          <- Solo el mejor (opcional)
                            opt_summary.json
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from tools.optimize.datasets import DatasetSpec, WindowSlice, iter_windows
from tools.optimize.optimizers import (
    BayesOptimizer,
    GridSearchOptimizer,
    Optimizer,
    RandomSearchOptimizer,
)

OPTIMIZER_MAP: dict[str, type[Optimizer]] = {
    "grid": GridSearchOptimizer,
    "random": RandomSearchOptimizer,
    "bayes": BayesOptimizer,
}


@dataclass
class TrialResult:
    params: dict[str, Any]
    score: float
    metrics: dict[str, Any]
    run_dir: Path


@dataclass
class StrategyTarget:
    """
    Representa una estrategia optimizable.

    Debe proveer:
        - name: identificador (momentum, vol_breakout, etc.)
        - space: diccionario compatible con los optimizadores (Choice, Integer, etc.)
        - evaluate(): ejecuta un backtest/stream usando params + dataset y devuelve TrialResult.
    """

    name: str
    space: dict[str, Any]
    evaluation_fn: Callable[[dict[str, Any], WindowSlice, Path], TrialResult]
    preprocess_params: Callable[[dict[str, Any]], dict[str, Any]] = lambda p: p

    def evaluate(self, params: dict[str, Any], window: WindowSlice, out_dir: Path) -> TrialResult:
        return self.evaluation_fn(params, window, out_dir)


@dataclass
class OptimizationConfig:
    dataset: DatasetSpec
    windows: Sequence[str | tuple[Any, Any] | dict]
    optimizer_name: str
    max_trials: int | None = None
    random_state: int | None = None
    maximize: bool = True
    target_metric: str = "return_total"
    out_root: Path = Path("runs_opt")
    extra_optimizer_kwargs: dict[str, Any] = field(default_factory=dict)
    min_improvement: float | None = None
    patience: int | None = None
    save_best_only: bool = True  # NUEVO: solo guardar el mejor trial completo


class OptimizationRunner:
    def __init__(self, target: StrategyTarget, config: OptimizationConfig) -> None:
        self.target = target
        self.config = config

    def _make_optimizer(self) -> Optimizer:
        cls = OPTIMIZER_MAP.get(self.config.optimizer_name.lower())
        if cls is None:
            raise KeyError(
                f"Optimizer '{self.config.optimizer_name}' no soportado. "
                f"Disponibles: {sorted(OPTIMIZER_MAP)}"
            )
        return cls(
            self.target.space,
            max_trials=self.config.max_trials,
            random_state=self.config.random_state,
            maximize=self.config.maximize,
            **self.config.extra_optimizer_kwargs,
        )

    def _prepare_window_dir(
        self, window_label: str, optimizer_name: str, builder_name: str = "default"
    ) -> Path:
        """Crea el directorio para una ventana completa (todos los trials)."""
        window_dir = (
            self.config.out_root / builder_name / self.target.name / optimizer_name / window_label
        )
        window_dir.mkdir(parents=True, exist_ok=True)
        return window_dir

    def run(self, builder_name: str = "default") -> list[TrialResult]:
        results: list[TrialResult] = []
        for window in iter_windows(self.config.dataset, self.config.windows):
            optimizer = self._make_optimizer()
            print(
                f"[Optimizer] Ventana '{window.label}' "
                f"({datetime.fromtimestamp(window.start_ts, tz=UTC)} → "
                f"{datetime.fromtimestamp(window.end_ts, tz=UTC)})"
            )
            self._run_window(window, optimizer, results, builder_name)
        return results

    def _run_window(
        self,
        window: WindowSlice,
        optimizer: Optimizer,
        results: list[TrialResult],
        builder_name: str,
    ) -> None:
        best_score = None
        stagnation = 0
        patience = self.config.patience or 0
        min_improv = self.config.min_improvement or 0.0

        window_dir = self._prepare_window_dir(window.label, optimizer.name, builder_name)
        all_trials: list[dict[str, Any]] = []
        best_trial: TrialResult | None = None

        while True:
            if optimizer.should_stop():
                print(f"[Optimizer] Ventana '{window.label}': límite de trials alcanzado.")
                break
            try:
                params = optimizer.ask()
            except StopIteration:
                print(f"[Optimizer] Ventana '{window.label}': sin más combinaciones.")
                break

            trial_idx = optimizer.trials_done + 1
            params = self.target.preprocess_params(dict(params))
            formatted = self._format_params(params)
            print(f"[Optimizer] [{window.label}] Trial {trial_idx} → {formatted}")

            # Usar directorio temporal para este trial (se borrará si no es el mejor)
            temp_dir = window_dir / f"_temp_trial_{trial_idx}"
            temp_dir.mkdir(parents=True, exist_ok=True)

            trial = self.target.evaluate(params, window, temp_dir)
            optimizer.tell(params, trial.score)

            print(
                f"[Optimizer] [{window.label}] score={trial.score:.6f} "
                f"bars={trial.metrics.get('bars_processed')}"
            )

            # Guardar info del trial en la lista consolidada
            trial_info = {
                "trial_idx": trial_idx,
                "params": params,
                "score": trial.score,
                "metrics": trial.metrics,
            }
            all_trials.append(trial_info)

            # Actualizar mejor trial
            if (
                best_trial is None
                or (self.config.maximize and trial.score > best_trial.score)
                or (not self.config.maximize and trial.score < best_trial.score)
            ):
                # Borrar el directorio del anterior mejor (si existe y save_best_only=True)
                if self.config.save_best_only and best_trial is not None:
                    try:
                        import shutil

                        if best_trial.run_dir.exists():
                            shutil.rmtree(best_trial.run_dir)
                    except Exception:
                        pass

                # Renombrar temp a best_trial
                best_dir = window_dir / "best_trial"
                if best_dir.exists():
                    import shutil

                    shutil.rmtree(best_dir)
                temp_dir.rename(best_dir)

                best_trial = TrialResult(
                    params=trial.params,
                    score=trial.score,
                    metrics=trial.metrics,
                    run_dir=best_dir,
                )
            else:
                # No es el mejor, borrar temp
                try:
                    import shutil

                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

            # Check improvement
            if (
                best_score is None
                or (self.config.maximize and trial.score > best_score + min_improv)
                or (not self.config.maximize and trial.score < best_score - min_improv)
            ):
                best_score = trial.score
                stagnation = 0
            else:
                stagnation += 1
                if patience and stagnation >= patience:
                    print(
                        f"[Optimizer] Ventana '{window.label}': mejora marginal < {min_improv}, "
                        f"deteniendo tras {stagnation} intentos."
                    )
                    break

            results.append(trial)

        # Guardar archivo consolidado con todos los trials
        self._save_consolidated(window_dir, window, optimizer, all_trials, best_trial)

    def _save_consolidated(
        self,
        window_dir: Path,
        window: WindowSlice,
        optimizer: Optimizer,
        all_trials: list[dict[str, Any]],
        best_trial: TrialResult | None,
    ) -> None:
        """Guarda todos los trials en un único archivo JSON."""
        consolidated = {
            "strategy": self.target.name,
            "optimizer": optimizer.name,
            "window": {
                "label": window.label,
                "start_ts": window.start_ts,
                "end_ts": window.end_ts,
                "ts_unit": window.ts_unit,
            },
            "total_trials": len(all_trials),
            "trials": all_trials,
        }

        if best_trial:
            consolidated["best_trial"] = {
                "params": best_trial.params,
                "score": best_trial.score,
                "metrics": best_trial.metrics,
            }

        output_path = window_dir / "all_trials.json"
        output_path.write_text(json.dumps(consolidated, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[Optimizer] Guardado consolidado: {output_path}")

    @staticmethod
    def _format_params(params: dict[str, Any]) -> dict[str, Any]:
        formatted: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, float):
                formatted[key] = float(f"{value:.6f}")
            else:
                formatted[key] = value
        return formatted
