"""Motor agroclimático: Open-Meteo (reanálisis ERA5 + pronóstico).

Se usa ERA5 en vez de la precipitación satelital tipo GPM porque a escala de
lote el reanálisis horario tiene menos ruido y llega hasta ayer, sin claves
de API ni cuotas.

Todas las funciones devuelven DataFrames con el mismo esquema de columnas,
así el resto de la app no necesita saber de dónde vino cada fila.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from ..cache import disk_cache
from ..config import SETTINGS

log = logging.getLogger(__name__)

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "et0_fao_evapotranspiration",
    "shortwave_radiation_sum",
    "wind_speed_10m_max",
    # Tormentas: la ráfaga es la que voltea un cultivo, no el viento medio, y el
    # código WMO distingue tormenta (95) de tormenta con granizo (96 y 99).
    "wind_gusts_10m_max",
    "weather_code",
    "precipitation_hours",
]

# Sólo disponibles en el pronóstico; en el archivo vuelven vacías
FORECAST_ONLY_VARS = ["precipitation_probability_max", "cape_max"]

RENAME = {
    "time": "date",
    "temperature_2m_max": "tmax",
    "temperature_2m_min": "tmin",
    "temperature_2m_mean": "tmean",
    "precipitation_sum": "precip_mm",
    "et0_fao_evapotranspiration": "et0_mm",
    "shortwave_radiation_sum": "rad_mj",
    "wind_speed_10m_max": "wind_kmh",
    "wind_gusts_10m_max": "gust_kmh",
    "weather_code": "wmo",
    "precipitation_hours": "precip_horas",
    "precipitation_probability_max": "precip_prob",
    "cape_max": "cape",
}

COLUMNS = ["date", "tmax", "tmin", "tmean", "precip_mm", "et0_mm", "rad_mj", "wind_kmh",
           "gust_kmh", "wmo", "precip_horas", "source"]


class WeatherError(RuntimeError):
    """Error del servicio climático, con mensaje presentable al usuario."""


def _get(url: str, params: dict) -> dict:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=SETTINGS.request_timeout_s)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:  # límite de tasa: reintentamos con espera
                import time

                time.sleep(2 * (attempt + 1))
                continue
            raise WeatherError(f"Open-Meteo respondió HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as exc:
            if attempt == 2:
                raise WeatherError(f"No se pudo consultar el servicio climático: {exc}") from exc
    raise WeatherError("El servicio climático no respondió tras 3 intentos.")


def _to_frame(payload: dict, source: str) -> pd.DataFrame:
    daily = payload.get("daily")
    if not daily:
        raise WeatherError("La respuesta climática no trae datos diarios.")
    df = pd.DataFrame(daily).rename(columns=RENAME)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["source"] = source
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    extra = [c for c in df.columns if c not in COLUMNS]
    return df[COLUMNS + extra].sort_values("date").reset_index(drop=True)


# --------------------------------------------------------------------------
# Series
# --------------------------------------------------------------------------
@disk_cache("wx-archive", ttl_hours=12)
def archive(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Serie diaria observada (reanálisis ERA5) entre dos fechas."""
    payload = _get(SETTINGS.weather_archive_url, {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": str(start), "end_date": str(end),
        "daily": ",".join(DAILY_VARS), "timezone": "auto",
    })
    return _to_frame(payload, "observado")


@disk_cache("wx-forecast", ttl_hours=3)
def forecast(lat: float, lon: float, days: int | None = None) -> pd.DataFrame:
    """Pronóstico diario para los próximos días."""
    days = days or SETTINGS.forecast_days
    payload = _get(SETTINGS.weather_forecast_url, {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "daily": ",".join(DAILY_VARS + FORECAST_ONLY_VARS),
        "forecast_days": min(16, days), "timezone": "auto",
    })
    return _to_frame(payload, "pronóstico")


@disk_cache("wx-climatology", ttl_hours=24 * 30)
def climatology(lat: float, lon: float) -> pd.DataFrame:
    """Normales 1991–2020 por día del año, suavizadas con ventana de ±7 días.

    Devuelve, por día juliano: media y percentiles 20/80 de temperatura y de
    lluvia acumulada de 30 días, que es la referencia útil para decir si la
    campaña viene seca o húmeda.
    """
    df = archive(lat, lon, date.fromisoformat(SETTINGS.climatology_start),
                 date.fromisoformat(SETTINGS.climatology_end))
    d = df.copy()
    d["dt"] = pd.to_datetime(d["date"])
    d["doy"] = d["dt"].dt.dayofyear.clip(upper=365)
    d["year"] = d["dt"].dt.year
    d["p30"] = d["precip_mm"].rolling(30, min_periods=15).sum()

    grp = d.groupby("doy")
    out = pd.DataFrame({
        "doy": sorted(d["doy"].unique()),
        "tmax_norm": grp["tmax"].mean().values,
        "tmin_norm": grp["tmin"].mean().values,
        "tmean_norm": grp["tmean"].mean().values,
        "et0_norm": grp["et0_mm"].mean().values,
        "precip_norm": grp["precip_mm"].mean().values,
        "p30_norm": grp["p30"].mean().values,
        "p30_p20": grp["p30"].quantile(0.20).values,
        "p30_p80": grp["p30"].quantile(0.80).values,
    })
    # Suavizado circular: el día 365 y el día 1 son vecinos
    for col in out.columns[1:]:
        s = pd.concat([out[col]] * 3, ignore_index=True)
        out[col] = s.rolling(15, center=True, min_periods=1).mean().iloc[len(out):2 * len(out)].values
    return out


@disk_cache("wx-bundle", ttl_hours=6)
def timeline(lat: float, lon: float, start: date, end: date,
             include_forecast: bool = True) -> pd.DataFrame:
    """Serie continua observado + pronóstico, sin días duplicados.

    ERA5 tiene ~5 días de latencia; el pronóstico rellena esa cola y agrega
    los próximos días, que es lo que sirve para decidir.
    """
    today = date.today()
    obs_end = min(end, today)
    frames: list[pd.DataFrame] = []
    if start <= obs_end:
        frames.append(archive(lat, lon, start, obs_end))

    if include_forecast:
        try:
            fc = forecast(lat, lon)
            frames.append(fc)
        except WeatherError as exc:  # el pronóstico es opcional
            log.warning("Sin pronóstico: %s", exc)

    if not frames:
        raise WeatherError("No hay datos climáticos para el período pedido.")

    df = pd.concat(frames, ignore_index=True)
    # Ante duplicados gana la observación; el pronóstico sólo rellena huecos
    df["_prio"] = (df["source"] == "observado").astype(int)
    df = (df.sort_values(["date", "_prio"]).drop_duplicates("date", keep="last")
            .drop(columns="_prio").reset_index(drop=True))
    # Recorte a la ventana pedida. Sin esto, el pronóstico (que siempre arranca
    # hoy) agrega días sueltos fuera del período y deja un hueco en el medio,
    # que después rompe cualquier cálculo que asuma días consecutivos.
    df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

    # ERA5 puede dejar algún hueco puntual: interpolamos temperatura, nunca lluvia
    for col in ("tmax", "tmin", "tmean", "et0_mm", "rad_mj"):
        df[col] = pd.to_numeric(df[col], errors="coerce").interpolate(limit=3)
    df["precip_mm"] = pd.to_numeric(df["precip_mm"], errors="coerce").fillna(0.0)
    # Las variables de tormenta NO se interpolan: un día sin dato de ráfaga no
    # es un día sin ráfaga, y un código WMO inventado sería un evento inventado.
    for col in ("gust_kmh", "wmo", "precip_horas"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def attach_climatology(df: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    """Suma a la serie diaria las normales del lugar para comparar contra la historia."""
    try:
        clim = climatology(lat, lon)
    except WeatherError as exc:
        log.warning("Sin climatología: %s", exc)
        return df
    out = df.copy()
    out["doy"] = pd.to_datetime(out["date"]).dt.dayofyear.clip(upper=365)
    return out.merge(clim, on="doy", how="left")


def summarize(df: pd.DataFrame) -> dict[str, float]:
    """Resumen del período en números redondos, listo para tarjetas."""
    obs = df[df["source"] == "observado"]
    if obs.empty:
        obs = df
    return {
        "lluvia_total_mm": float(obs["precip_mm"].sum()),
        "dias_con_lluvia": int((obs["precip_mm"] >= 1.0).sum()),
        "lluvia_max_diaria_mm": float(obs["precip_mm"].max() or 0),
        "et0_total_mm": float(obs["et0_mm"].sum()),
        "balance_mm": float(obs["precip_mm"].sum() - obs["et0_mm"].sum()),
        "tmax_media": float(obs["tmax"].mean()),
        "tmin_media": float(obs["tmin"].mean()),
        "dias_helada": int((obs["tmin"] <= 0).sum()),
        "dias_calor_35": int((obs["tmax"] >= 35).sum()),
    }
