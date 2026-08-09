"""Comparación contra la historia del propio lote.

Un NDVI de 0,62 no dice nada por sí solo. Dice mucho si sabemos que en los
últimos seis años, a los mismos días desde la siembra, ese lote estuvo entre
0,70 y 0,81. Este módulo construye esa referencia y ubica la campaña actual
dentro de ella.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .timeseries import build_curve


def season_window(sowing: date, cycle_days: int, year_offset: int = 0) -> tuple[date, date]:
    """Ventana de una campaña, desplazada `year_offset` años hacia atrás."""
    try:
        anchor = sowing.replace(year=sowing.year - year_offset)
    except ValueError:  # 29 de febrero
        anchor = sowing.replace(year=sowing.year - year_offset, day=28)
    return anchor - timedelta(days=20), anchor + timedelta(days=cycle_days + 25)


def build_history(
    fetch_series,
    geometry: dict,
    sowing: date,
    cycle_days: int,
    index_key: str = "NDVI",
    n_years: int = 6,
    smoothing_days: int = 21,
    progress=None,
) -> pd.DataFrame:
    """Matriz de campañas anteriores alineadas por días desde la siembra.

    `fetch_series(geometry, start, end, index_key)` es inyectado para que este
    módulo no dependa del motor satelital (y sea testeable sin red).
    """
    frames = []
    for k in range(1, n_years + 1):
        start, end = season_window(sowing, cycle_days, k)
        if progress:
            progress(k / n_years, f"Campaña {start.year}/{str(end.year)[-2:]}")
        try:
            raw = fetch_series(geometry, start, end, index_key)
        except Exception:  # una campaña sin datos no debe frenar el resto
            continue
        if raw is None or raw.empty:
            continue
        curve = build_curve(raw, "mean", smoothing_days, start, end)
        anchor = season_window(sowing, cycle_days, k)[0] + timedelta(days=20)
        curve["das"] = [(d - anchor).days for d in curve["date"]]
        curve["campaña"] = f"{start.year}/{str(end.year)[-2:]}"
        frames.append(curve[["das", "smooth", "campaña"]])

    if not frames:
        return pd.DataFrame(columns=["das", "smooth", "campaña"])
    return pd.concat(frames, ignore_index=True)


def envelope(history: pd.DataFrame, smooth_window: int = 7) -> pd.DataFrame:
    """Banda histórica por día desde la siembra: mediana y percentiles 10/25/75/90."""
    if history.empty:
        return pd.DataFrame(columns=["das", "p10", "p25", "p50", "p75", "p90", "n"])
    g = history.groupby("das")["smooth"]
    out = pd.DataFrame({
        "das": sorted(history["das"].unique()),
        "p10": g.quantile(0.10).values, "p25": g.quantile(0.25).values,
        "p50": g.median().values, "p75": g.quantile(0.75).values,
        "p90": g.quantile(0.90).values, "n": g.count().values,
    })
    for c in ("p10", "p25", "p50", "p75", "p90"):
        out[c] = out[c].rolling(smooth_window, center=True, min_periods=1).mean()
    return out


def rank_current(curve: pd.DataFrame, sowing: date, history: pd.DataFrame) -> pd.DataFrame:
    """Ubica cada día de la campaña actual dentro de la distribución histórica."""
    if curve.empty or history.empty:
        return pd.DataFrame(columns=["date", "das", "valor", "percentil", "z", "anomalia"])

    cur = curve.copy()
    cur["das"] = [(d - sowing).days for d in cur["date"]]
    col = "smooth" if "smooth" in cur.columns else "value"

    groups = {das: g["smooth"].to_numpy(dtype=float) for das, g in history.groupby("das")}
    rows = []
    for _, r in cur.iterrows():
        ref = groups.get(int(r["das"]))
        if ref is None or len(ref) < 2:
            continue
        val = float(r[col])
        pct = float((ref < val).mean() * 100)
        mu, sd = float(np.nanmean(ref)), float(np.nanstd(ref))
        rows.append({
            "date": r["date"], "das": int(r["das"]), "valor": val,
            "percentil": pct, "z": (val - mu) / sd if sd else 0.0,
            "anomalia": val - mu, "n_campañas": len(ref),
        })
    return pd.DataFrame(rows)


def classify(percentile: float) -> tuple[str, str]:
    """Etiqueta y severidad para un percentil histórico."""
    if percentile >= 85:
        return "Muy por encima de la historia", "good"
    if percentile >= 65:
        return "Por encima de la historia", "good"
    if percentile >= 35:
        return "En línea con la historia", "info"
    if percentile >= 15:
        return "Por debajo de la historia", "warning"
    return "Muy por debajo de la historia", "serious"


def summarize(rank: pd.DataFrame, last_days: int = 21) -> dict:
    """Resumen del estado actual respecto de la historia."""
    if rank.empty:
        return {}
    tail = rank.tail(last_days)
    pct = float(tail["percentil"].mean())
    label, severity = classify(pct)
    return {
        "percentil_actual": round(pct, 1),
        "z_actual": round(float(tail["z"].mean()), 2),
        "anomalia_actual": round(float(tail["anomalia"].mean()), 3),
        "campañas_comparadas": int(rank["n_campañas"].max()),
        "etiqueta": label,
        "severidad": severity,
        "dias_bajo_p25": int((rank["percentil"] < 25).sum()),
    }


def spatial_anomaly(current: dict, reference: dict) -> dict:
    """Mapa de diferencia entre la campaña actual y la referencia histórica.

    Las dos grillas tienen que venir del mismo pedido de descarga (mismo lote,
    misma escala) para que los píxeles se correspondan.
    """
    a = np.asarray(current["values"], dtype="float32")
    b = np.asarray(reference["values"], dtype="float32")
    if a.shape != b.shape:
        raise ValueError("Las grillas no coinciden; no se pueden restar píxel a píxel.")
    diff = a - b
    return {
        "values": diff, "valid": np.isfinite(diff), "transform": current["transform"],
        "crs": current["crs"], "index": f"Δ {current.get('index', '')}",
        "vmin": float(-np.nanpercentile(np.abs(diff), 95) or 0.1),
        "vmax": float(np.nanpercentile(np.abs(diff), 95) or 0.1),
    }
