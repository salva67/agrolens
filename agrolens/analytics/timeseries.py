"""Reconstrucción de la curva del índice.

Las observaciones satelitales llegan irregulares (nubes, órbitas) y con
residuos que tiran los valores hacia abajo. Acá se pasa de "puntos sueltos"
a una curva diaria utilizable: grilla diaria → filtro de outliers bajos →
suavizado Savitzky-Golay.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def to_daily(df: pd.DataFrame, value_col: str = "mean",
             start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Interpola las observaciones a una grilla diaria continua."""
    if df.empty:
        return pd.DataFrame(columns=["date", "value", "observed"])
    d = df[["date", value_col]].dropna().sort_values("date").copy()
    d["date"] = pd.to_datetime(d["date"])
    idx = pd.date_range(start or d["date"].min(), end or d["date"].max(), freq="D")
    s = d.set_index("date")[value_col].reindex(idx)
    observed = s.notna()
    s = s.interpolate(method="time", limit_direction="both")
    return pd.DataFrame({"date": idx.date, "value": s.values, "observed": observed.values})


def drop_low_outliers(df: pd.DataFrame, value_col: str = "mean", k: float = 2.5) -> pd.DataFrame:
    """Descarta caídas aisladas hacia abajo (típicamente nube o sombra residual).

    Sólo se filtra hacia abajo: una subida brusca del índice es real
    (emergencia, riego, un corte de pastura), una bajada de un día que se
    recupera de inmediato casi nunca lo es.
    """
    if len(df) < 5:
        return df
    d = df.sort_values("date").reset_index(drop=True).copy()
    v = d[value_col].to_numpy(dtype=float)
    med = pd.Series(v).rolling(5, center=True, min_periods=2).median().to_numpy()
    resid = v - med
    scale = np.nanmedian(np.abs(resid)) * 1.4826 or np.nanstd(resid) or 1e-6
    keep = resid > -k * scale
    keep[0] = keep[-1] = True
    return d[keep].reset_index(drop=True)


def smooth(daily: pd.DataFrame, window_days: int = 21, polyorder: int = 2) -> pd.DataFrame:
    """Savitzky-Golay sobre la grilla diaria: conserva picos, quita ruido."""
    if daily.empty:
        return daily
    from scipy.signal import savgol_filter

    out = daily.copy()
    n = len(out)
    win = min(window_days if window_days % 2 else window_days + 1, n if n % 2 else n - 1)
    if win < 5 or win <= polyorder:
        out["smooth"] = out["value"]
        return out
    out["smooth"] = savgol_filter(out["value"].to_numpy(dtype=float), win, polyorder)
    return out


def build_curve(df: pd.DataFrame, value_col: str = "mean", window_days: int = 21,
                start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Pipeline completo: filtro → grilla diaria → suavizado."""
    clean = drop_low_outliers(df, value_col)
    daily = to_daily(clean, value_col, start, end)
    return smooth(daily, window_days)


def rolling_stat(df: pd.DataFrame, col: str, days: int, how: str = "sum") -> pd.Series:
    s = pd.Series(df[col].to_numpy(dtype=float), index=pd.to_datetime(df["date"]))
    r = s.rolling(f"{days}D", min_periods=1)
    return getattr(r, how)().reset_index(drop=True)


def trend(curve: pd.DataFrame, days: int = 21) -> dict[str, float]:
    """Pendiente reciente del índice, en unidades por semana."""
    if len(curve) < 5:
        return {"slope_week": 0.0, "delta": 0.0, "r2": 0.0}
    tail = curve.tail(days)
    x = np.arange(len(tail), dtype=float)
    y = tail["smooth"].to_numpy(dtype=float) if "smooth" in tail else tail["value"].to_numpy(float)
    if np.allclose(y, y[0]):
        return {"slope_week": 0.0, "delta": 0.0, "r2": 1.0}
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    return {
        "slope_week": float(slope * 7),
        "delta": float(y[-1] - y[0]),
        "r2": float(1 - ss_res / ss_tot),
    }


def integral(curve: pd.DataFrame, floor: float = 0.15,
             start: date | None = None, end: date | None = None) -> float:
    """Integral de la curva por encima de un piso (proxy de biomasa acumulada)."""
    if curve.empty:
        return 0.0
    c = curve
    if start:
        c = c[c["date"] >= start]
    if end:
        c = c[c["date"] <= end]
    if c.empty:
        return 0.0
    col = "smooth" if "smooth" in c else "value"
    return float(np.clip(c[col].to_numpy(dtype=float) - floor, 0, None).sum())


def gap_report(df: pd.DataFrame, expected_days: int = 5) -> dict[str, float]:
    """Cuán bien cubierta está la temporada por observaciones válidas."""
    if len(df) < 2:
        return {"n_obs": len(df), "gap_max_dias": 0, "gap_medio_dias": 0.0, "cobertura_pct": 0.0}
    d = pd.to_datetime(df["date"]).sort_values()
    gaps = d.diff().dt.days.dropna()
    span = max(1, (d.max() - d.min()).days)
    return {
        "n_obs": int(len(df)),
        "gap_max_dias": int(gaps.max()),
        "gap_medio_dias": float(gaps.mean()),
        "cobertura_pct": float(min(100.0, 100 * len(df) / (span / expected_days))),
    }


def align_by_days_after_sowing(df: pd.DataFrame, sowing: date, value_col: str = "mean") -> pd.DataFrame:
    """Reindexa una campaña por días desde la siembra, para comparar años entre sí."""
    out = df.copy()
    out["das"] = [(pd.Timestamp(d).date() - sowing).days for d in out["date"]]
    return out[["das", value_col]].rename(columns={value_col: "value"})


def season_bounds(sowing: date, cycle_days: int, margin_before: int = 30,
                  margin_after: int = 30) -> tuple[date, date]:
    return sowing - timedelta(days=margin_before), sowing + timedelta(days=cycle_days + margin_after)
