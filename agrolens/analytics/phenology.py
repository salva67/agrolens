"""Fenología a partir de la curva del índice.

Se extraen los hitos que un agrónomo mira primero: cuándo arrancó la
temporada, cuándo llegó al pico, cuándo empezó a caer y cuánta biomasa
acumuló. El método es el umbral dinámico de Jönsson & Eklundh: los puntos de
inflexión se definen como una fracción de la amplitud de la propia curva, no
como un valor fijo de NDVI, así funciona igual en un trigo que en una soja.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..models import Phenology


def extract(curve: pd.DataFrame, threshold: float = 0.35,
            min_amplitude: float = 0.12) -> Phenology:
    """Calcula SOS / POS / EOS y métricas derivadas sobre la curva suavizada."""
    if curve.empty or len(curve) < 10:
        return Phenology()

    col = "smooth" if "smooth" in curve.columns else "value"
    y = curve[col].to_numpy(dtype=float)
    dates = list(pd.to_datetime(curve["date"]).dt.date)

    i_peak = int(np.nanargmax(y))
    peak = float(y[i_peak])
    base = float(np.nanmin(y))
    amp = peak - base
    if amp < min_amplitude:
        # Curva plana: no hay temporada identificable (barbecho, pastura estable)
        return Phenology(pos=dates[i_peak], peak_value=peak, integral=_integral(y, base))

    level = base + threshold * amp
    sos = _cross(y, dates, level, i_peak, direction="up")
    eos = _cross(y, dates, level, i_peak, direction="down")

    green_rate = sen_rate = None
    if sos:
        d = (dates[i_peak] - sos).days
        green_rate = float(amp * (1 - threshold) / d) if d > 0 else None
    if eos:
        d = (eos - dates[i_peak]).days
        sen_rate = float(-amp * (1 - threshold) / d) if d > 0 else None

    return Phenology(
        sos=sos, pos=dates[i_peak], eos=eos, peak_value=peak,
        integral=_integral(y, base), green_up_rate=green_rate, senescence_rate=sen_rate,
        length_days=(eos - sos).days if sos and eos else None,
    )


def _integral(y: np.ndarray, floor: float) -> float:
    return float(np.nansum(np.clip(y - floor, 0, None)))


def _cross(y: np.ndarray, dates: list[date], level: float, i_peak: int,
           direction: str) -> date | None:
    """Primer cruce del umbral antes (up) o después (down) del pico."""
    if direction == "up":
        idx = range(i_peak, 0, -1)
        for i in idx:
            if y[i - 1] <= level <= y[i]:
                return dates[i - 1]
        return None
    for i in range(i_peak, len(y) - 1):
        if y[i] >= level >= y[i + 1]:
            return dates[i + 1]
    return None


def describe(ph: Phenology, sowing: date | None = None) -> list[tuple[str, str]]:
    """Traduce las métricas a filas legibles para la UI y el PDF."""
    rows: list[tuple[str, str]] = []
    if ph.sos:
        extra = f" ({(ph.sos - sowing).days} días tras la siembra)" if sowing else ""
        rows.append(("Inicio de temporada", f"{ph.sos:%d/%m/%Y}{extra}"))
    if ph.pos:
        rows.append(("Pico de biomasa", f"{ph.pos:%d/%m/%Y}"))
    if ph.peak_value is not None:
        rows.append(("Valor en el pico", f"{ph.peak_value:.2f}"))
    if ph.eos:
        rows.append(("Fin de temporada", f"{ph.eos:%d/%m/%Y}"))
    if ph.length_days:
        rows.append(("Duración del ciclo verde", f"{ph.length_days} días"))
    if ph.integral is not None:
        rows.append(("Integral del índice", f"{ph.integral:.0f} unidades·día"))
    if ph.green_up_rate:
        rows.append(("Velocidad de crecimiento", f"{ph.green_up_rate * 7:+.3f} por semana"))
    if ph.senescence_rate:
        rows.append(("Velocidad de senescencia", f"{ph.senescence_rate * 7:+.3f} por semana"))
    return rows


def stage_timeline(curve: pd.DataFrame, gdd: pd.Series, crop) -> pd.DataFrame:
    """Asocia cada día de la curva con la etapa fenológica por suma térmica."""
    if curve.empty or gdd is None or len(gdd) == 0:
        return pd.DataFrame(columns=["date", "gdd", "etapa"])
    n = min(len(curve), len(gdd))
    acc = np.asarray(gdd)[:n]
    stages = [crop.stage_at(float(g)) for g in acc]
    return pd.DataFrame({
        "date": list(curve["date"])[:n],
        "gdd": acc,
        "etapa": [s.name if s else "" for s in stages],
    })
