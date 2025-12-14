# tools/optimize/optimizers.py
from __future__ import annotations

"""
Implementaciones de optimizadores genéricos para estrategias.

Se proveen tres enfoques:
    - GridSearchOptimizer: recorre exhaustivamente combinaciones discretas.
    - RandomSearchOptimizer: muestrea combinaciones al azar dentro de rangos.
    - BayesOptimizer: aproximación ligera basada en realimentar los mejores
      resultados (modela medias/desvíos de los top-k para nuevas propuestas).

Cada optimizador comparte una interfaz `ask()/tell()` para integrarse con el
runner principal sin acoplarse al detalle de la estrategia.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import itertools
import math
import random
from typing import Any

# --------------------------- Espacio de parámetros ---------------------------


class ParameterSpec:
    """Tipo base para specs de parámetros."""

    def grid_values(self) -> Sequence[Any]:
        raise NotImplementedError

    def sample(self, rng: random.Random) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class Choice(ParameterSpec):
    values: Sequence[Any]

    def grid_values(self) -> Sequence[Any]:
        return list(self.values)

    def sample(self, rng: random.Random) -> Any:
        return rng.choice(list(self.values))


@dataclass(frozen=True)
class Integer(ParameterSpec):
    low: int
    high: int
    step: int = 1

    def grid_values(self) -> Sequence[int]:
        return list(range(self.low, self.high + self.step, self.step))

    def sample(self, rng: random.Random) -> int:
        steps = max(1, int((self.high - self.low) / self.step))
        idx = rng.randint(0, steps)
        return self.low + idx * self.step


@dataclass(frozen=True)
class Continuous(ParameterSpec):
    low: float
    high: float

    def grid_values(self) -> Sequence[float]:
        raise ValueError("Continuous no soporta grid. Usa Choice o Integer discretizado.")

    def sample(self, rng: random.Random) -> float:
        return rng.uniform(self.low, self.high)


@dataclass(frozen=True)
class LogUniform(ParameterSpec):
    low: float
    high: float

    def grid_values(self) -> Sequence[float]:
        raise ValueError("LogUniform no soporta grid. Usa Choice si necesitas discreto.")

    def sample(self, rng: random.Random) -> float:
        lo = math.log(self.low)
        hi = math.log(self.high)
        return math.exp(rng.uniform(lo, hi))


@dataclass(frozen=True)
class StepContinuous(ParameterSpec):
    low: float
    high: float
    step: float

    def grid_values(self) -> Sequence[float]:
        values = []
        current = self.low
        while current <= self.high + 1e-12:
            values.append(round(current, 10))
            current += self.step
        return values

    def sample(self, rng: random.Random) -> float:
        val = rng.uniform(self.low, self.high)
        return self._round(val)

    def _round(self, val: float) -> float:
        steps = round((val - self.low) / self.step)
        rounded = self.low + steps * self.step
        return float(min(self.high, max(self.low, rounded)))


def _ensure_spec(value: Any) -> ParameterSpec:
    if isinstance(value, ParameterSpec):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return Choice(list(value))
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(v, (int, float)) for v in value)
    ):
        lo, hi = value
        if isinstance(lo, int) and isinstance(hi, int):
            return Integer(lo, hi)
        return Continuous(float(lo), float(hi))
    raise TypeError(
        f"No puedo convertir '{value}' en ParameterSpec. Usa Choice/Integer/Continuous."
    )


# --------------------------- Base Optimizer ---------------------------------


class Optimizer:
    name = "base"

    def __init__(
        self,
        space: dict[str, ParameterSpec | Sequence[Any] | tuple[int | float, int | float]],
        *,
        max_trials: int | None = None,
        random_state: int | None = None,
        maximize: bool = True,
    ) -> None:
        self.space: dict[str, ParameterSpec] = {k: _ensure_spec(v) for k, v in space.items()}
        self.max_trials = max_trials
        self.random = random.Random(random_state)
        self.maximize = maximize
        self.history: list[tuple[dict[str, Any], float]] = []
        self._trials_done = 0

    def ask(self) -> dict[str, Any]:
        raise NotImplementedError

    def tell(self, params: dict[str, Any], score: float) -> None:
        self.history.append((params, score))
        self._trials_done += 1

    @property
    def trials_done(self) -> int:
        return self._trials_done

    def should_stop(self) -> bool:
        if self.max_trials is None:
            return False
        return self.trials_done >= self.max_trials

    def best(self) -> tuple[dict[str, Any], float] | None:
        if not self.history:
            return None
        return (
            max(self.history, key=lambda item: item[1])
            if self.maximize
            else min(self.history, key=lambda item: item[1])
        )


# --------------------------- Grid Search ------------------------------------


class GridSearchOptimizer(Optimizer):
    name = "grid"

    def __init__(
        self,
        space: dict[str, ParameterSpec | Sequence[Any] | tuple[int | float, int | float]],
        **kwargs,
    ):
        super().__init__(space, **kwargs)
        values_per_key: list[Sequence[Any]] = []
        for key, spec in self.space.items():
            values_per_key.append(spec.grid_values())
        self._grid: Iterator[tuple[Any, ...]] = itertools.product(*values_per_key)
        self._keys = list(self.space.keys())

    def ask(self) -> dict[str, Any]:
        if self.should_stop():
            raise StopIteration("GridSearch agotado")
        try:
            combo = next(self._grid)
        except StopIteration as exc:
            raise StopIteration("No quedan combinaciones en el grid") from exc
        return dict(zip(self._keys, combo))


# --------------------------- Random Search ----------------------------------


class RandomSearchOptimizer(Optimizer):
    name = "random"

    def ask(self) -> dict[str, Any]:
        if self.should_stop():
            raise StopIteration("Límite de random search alcanzado")
        params = {}
        for key, spec in self.space.items():
            params[key] = spec.sample(self.random)
        return params


# --------------------------- "Bayesian" Search -------------------------------


class BayesOptimizer(Optimizer):
    """
    Aproximación ligera inspirada en Bayesian Optimization.

    - Realiza muestras aleatorias durante `initial_random`.
    - Luego modela cada parámetro con la media/desvío de los mejores `top_k`
      resultados, muestreando alrededor de esas regiones para explotar.
    - Para categorías usa distribución empírica de frecuencias.
    """

    name = "bayes"

    def __init__(
        self,
        space: dict[str, ParameterSpec | Sequence[Any] | tuple[int | float, int | float]],
        *,
        initial_random: int = 5,
        top_k: int = 5,
        **kwargs,
    ) -> None:
        super().__init__(space, **kwargs)
        self.initial_random = initial_random
        self.top_k = top_k

    def ask(self) -> dict[str, Any]:
        if self.should_stop():
            raise StopIteration("Límite de bayes search alcanzado")
        if self.trials_done < self.initial_random or len(self.history) < self.initial_random:
            # fase aleatoria
            return {k: spec.sample(self.random) for k, spec in self.space.items()}
        top = sorted(self.history, key=lambda item: item[1], reverse=self.maximize)[: self.top_k]
        params: dict[str, Any] = {}
        for key, spec in self.space.items():
            values = [trial_params[key] for trial_params, _ in top if key in trial_params]
            if not values:
                params[key] = spec.sample(self.random)
                continue
            if isinstance(spec, Choice):
                freq: dict[Any, int] = {}
                for val in values:
                    freq[val] = freq.get(val, 0) + 1
                total = sum(freq.values())
                pick = self.random.uniform(0, total)
                cumulative = 0.0
                chosen = values[0]
                for val, count in freq.items():
                    cumulative += count
                    if pick <= cumulative:
                        chosen = val
                        break
                params[key] = chosen
            elif isinstance(spec, Integer):
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
                std = math.sqrt(max(variance, 1e-9))
                sample = int(round(self.random.gauss(mean, std)))
                params[key] = min(spec.high, max(spec.low, sample))
            elif isinstance(spec, (Continuous, LogUniform, StepContinuous)):
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
                std = math.sqrt(max(variance, 1e-9))
                sample = self.random.gauss(mean, std or (spec.high - spec.low) * 0.1)
                if isinstance(spec, LogUniform):
                    sample = max(spec.low, min(spec.high, sample))
                    params[key] = float(sample)
                elif isinstance(spec, StepContinuous):
                    params[key] = spec._round(sample)
                else:
                    params[key] = float(min(spec.high, max(spec.low, sample)))
            else:
                params[key] = spec.sample(self.random)
        return params
