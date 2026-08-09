"""Modo demostración: datos sintéticos verosímiles, sin red ni credenciales.

Sirve para probar la app, para desarrollar sin gastar cuota de Earth Engine y
para que la interfaz nunca quede en blanco cuando un servicio está caído.
Los datos son plausibles pero **no son reales**: la UI siempre lo aclara.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..crops import get_crop
from ..geo import area_ha, to_shape
from ..models import uniformity_score

RNG_SEED = 20260101


def _rng(geometry: dict, salt: int = 0) -> np.random.Generator:
    """Semilla derivada del lote: el mismo lote da siempre la misma demo."""
    c = to_shape(geometry).centroid
    seed = int(abs(c.x * 1000) + abs(c.y * 1000)) + RNG_SEED + salt
    return np.random.default_rng(seed)


def index_series(geometry: dict, start: date, end: date, index_key: str = "NDVI",
                 crop: str = "soja", sowing: date | None = None, **_: object) -> pd.DataFrame:
    """Serie sintética con forma de curva fenológica y pasadas cada 5 días."""
    rng = _rng(geometry, hash(index_key) % 997)
    crop_p = get_crop(crop)
    sowing = sowing or (start + timedelta(days=15))
    dates = pd.date_range(start, end, freq="5D").date
    rows = []
    for d in dates:
        t = (d - sowing).days / max(1, crop_p.cycle_days)
        if t < 0 or t > 1.25:
            base = 0.16 + rng.normal(0, 0.02)
        else:  # doble logística: crecimiento y senescencia
            up = 1 / (1 + np.exp(-12 * (t - 0.28)))
            down = 1 / (1 + np.exp(14 * (t - 0.85)))
            base = 0.15 + (crop_p.ndvi_peak - 0.15) * up * down + rng.normal(0, 0.025)
        if rng.random() < 0.25:  # una de cada cuatro pasadas se pierde por nubes
            continue
        mean = float(np.clip(base, 0.05, 0.95))
        std = float(0.03 + 0.10 * mean * rng.uniform(0.6, 1.4))
        rows.append({
            "date": d, "scene_id": f"DEMO_{d:%Y%m%d}", "cloud_scene_pct": float(rng.uniform(0, 40)),
            "valid_fraction": float(rng.uniform(0.75, 1.0)), "mean": mean,
            "median": mean + rng.normal(0, 0.01), "p10": mean - 1.3 * std, "p90": mean + 1.3 * std,
            "std": std, "min": max(0.02, mean - 3 * std), "max": min(0.98, mean + 2.5 * std),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["cv"] = df["std"] / df["mean"]
    df["uniformity"] = [uniformity_score(m, s) for m, s in zip(df["mean"], df["std"])]
    df["index"] = index_key
    return df


def raster(geometry: dict, index_key: str = "NDVI", mean: float = 0.65) -> dict:
    """Ráster sintético con estructura espacial (no ruido blanco)."""
    from rasterio.transform import from_origin

    rng = _rng(geometry, 7)
    ha = max(1.0, area_ha(geometry))
    n = int(np.clip(np.sqrt(ha * 10_000) / 10, 20, 200))
    # Campo suave: suma de gaussianas + gradiente, que imita zonas de manejo
    yy, xx = np.mgrid[0:n, 0:n] / n
    field = 0.35 * xx + 0.2 * yy
    for _ in range(4):
        cx, cy, s = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9), rng.uniform(0.08, 0.25)
        field += rng.uniform(-0.5, 0.6) * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s**2)))
    field = (field - field.mean()) / (field.std() or 1)
    values = np.clip(mean + field * 0.09 + rng.normal(0, 0.015, field.shape), 0.05, 0.95)

    minx, miny, maxx, maxy = to_shape(geometry).bounds
    res = (maxx - minx) / n
    mask_r = ((xx - 0.5) ** 2 + (yy - 0.5) ** 2) < 0.26  # recorte aproximado al lote
    values[~mask_r] = np.nan
    return {
        "values": values.astype("float32"), "valid": mask_r,
        "transform": from_origin(minx, maxy, res, res), "crs": "EPSG:4326",
        "scale": 10, "index": index_key, "demo": True,
    }


def weather(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Serie climática sintética con estacionalidad del hemisferio sur."""
    rng = np.random.default_rng(int(abs(lat * 100) + abs(lon * 100)) + RNG_SEED)
    dates = pd.date_range(start, end, freq="D")
    doy = dates.dayofyear.values
    season = np.cos(2 * np.pi * (doy - 15) / 365)  # máximo en enero
    tmean = 16 + 8 * season + rng.normal(0, 2.5, len(dates))
    tmax = tmean + rng.uniform(5, 9, len(dates))
    tmin = tmean - rng.uniform(5, 9, len(dates))
    wet = rng.random(len(dates)) < (0.16 + 0.10 * (-season))
    precip = np.where(wet, rng.gamma(1.6, 7.0, len(dates)), 0.0)
    et0 = np.clip(1.2 + 2.6 * (-season) + rng.normal(0, 0.5, len(dates)), 0.3, 9.0)
    return pd.DataFrame({
        "date": [d.date() for d in dates], "tmax": tmax, "tmin": tmin, "tmean": tmean,
        "precip_mm": precip, "et0_mm": et0, "rad_mj": et0 * 4.2,
        "wind_kmh": rng.uniform(6, 30, len(dates)),
        "source": ["observado" if d.date() <= date.today() else "pronóstico" for d in dates],
    })
