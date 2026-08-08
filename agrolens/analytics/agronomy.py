"""Agronomía: grados-día, balance hídrico, estreses y rendimiento estimado.

El balance hídrico sigue el esquema FAO-56 de un solo reservorio, con dos
mejoras respecto de la versión de manual:

  1. el coeficiente de cultivo (Kc) se deriva del NDVI observado cuando hay
     satélite, en lugar de asumir una curva teórica; así el balance "ve" una
     implantación fallida o un cultivo atrasado;
  2. el coeficiente de estrés (Ks) alimenta un índice de estrés diario que
     después se pondera por la ventana crítica de cada cultivo.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..crops import Crop


# --------------------------------------------------------------------------
# Grados-día
# --------------------------------------------------------------------------
def growing_degree_days(wx: pd.DataFrame, crop: Crop, sowing: date | None = None) -> pd.DataFrame:
    """GDD diarios y acumulados desde la siembra (método de promedio truncado)."""
    d = wx.copy()
    tmax = np.minimum(pd.to_numeric(d["tmax"], errors="coerce"), crop.t_upper)
    tmin = np.maximum(pd.to_numeric(d["tmin"], errors="coerce"), crop.t_base)
    tmin = np.minimum(tmin, crop.t_upper)
    d["gdd"] = np.clip((tmax + tmin) / 2 - crop.t_base, 0, None).fillna(0)
    if sowing is not None:
        d.loc[pd.to_datetime(d["date"]).dt.date < sowing, "gdd"] = 0.0
    d["gdd_acum"] = d["gdd"].cumsum()
    d["ciclo_frac"] = (d["gdd_acum"] / crop.gdd_cycle).clip(0, 1.3)
    d["etapa"] = [crop.stage_at(g).name if crop.stage_at(g) else "" for g in d["gdd_acum"]]
    return d


# --------------------------------------------------------------------------
# Balance hídrico
# --------------------------------------------------------------------------
def kc_from_ndvi(ndvi: float | np.ndarray, kc_max: float = 1.15) -> np.ndarray:
    """Kc a partir del NDVI: 0,15 de suelo desnudo a Kc_max en plena cobertura."""
    fcover = np.clip((np.asarray(ndvi, dtype=float) - 0.15) / (0.85 - 0.15), 0, 1)
    return 0.20 + (kc_max - 0.20) * fcover


def water_balance(
    wx: pd.DataFrame,
    crop: Crop,
    sowing: date | None = None,
    awc_mm: float = 150.0,
    initial_fill: float = 0.7,
    ndvi_curve: pd.DataFrame | None = None,
    depletion_frac: float = 0.55,
) -> pd.DataFrame:
    """Balance hídrico diario del perfil explorado.

    Devuelve, por día: agua almacenada, déficit acumulado, Kc, ETc, ET real,
    coeficiente de estrés Ks (1 = sin estrés) y drenaje.
    """
    d = wx.copy().reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    n = len(d)
    taw = float(awc_mm)  # agua total disponible
    raw = taw * depletion_frac  # agua fácilmente disponible

    precip = pd.to_numeric(d["precip_mm"], errors="coerce").fillna(0).to_numpy()
    et0 = pd.to_numeric(d["et0_mm"], errors="coerce").ffill().fillna(3.0).to_numpy()

    # Kc: desde NDVI si hay curva; si no, curva teórica por fracción del ciclo
    if ndvi_curve is not None and not ndvi_curve.empty:
        col = "smooth" if "smooth" in ndvi_curve.columns else "value"
        s = pd.Series(ndvi_curve[col].to_numpy(dtype=float),
                      index=pd.to_datetime(ndvi_curve["date"]))
        aligned = s.reindex(pd.to_datetime(d["date"])).interpolate(limit_direction="both")
        kc = kc_from_ndvi(aligned.to_numpy(), crop.kc_mid)
        kc = np.nan_to_num(kc, nan=crop.kc_ini)
        kc_source = "NDVI observado"
    else:
        gdd = growing_degree_days(d, crop, sowing)
        kc = np.array([crop.kc_at(f) for f in gdd["ciclo_frac"]])
        kc_source = "curva teórica FAO-56"

    if sowing is not None:
        pre = np.array([dd < sowing for dd in d["date"]])
        kc = np.where(pre, crop.kc_ini * 0.6, kc)  # barbecho: sólo evaporación

    asw = np.zeros(n)  # agua almacenada
    eta = np.zeros(n)
    etc = np.zeros(n)
    ks = np.ones(n)
    drain = np.zeros(n)
    runoff = np.zeros(n)

    store = taw * float(np.clip(initial_fill, 0, 1))
    for i in range(n):
        p = precip[i]
        ro = 0.15 * max(0.0, p - 40.0)  # escurrimiento grueso en lluvias intensas
        runoff[i] = ro
        store += p - ro
        if store > taw:
            drain[i] = store - taw
            store = taw
        etc[i] = kc[i] * et0[i]
        depletion = taw - store
        k = 1.0 if depletion <= (taw - raw) else max(0.0, (taw - depletion) / (taw - raw + 1e-9))
        ks[i] = float(np.clip(k, 0, 1))
        eta[i] = etc[i] * ks[i]
        store = max(0.0, store - eta[i])
        asw[i] = store

    out = d.copy()
    out["kc"] = kc
    out["etc_mm"] = etc
    out["eta_mm"] = eta
    out["ks"] = ks
    out["agua_util_mm"] = asw
    out["agua_util_pct"] = 100 * asw / taw
    out["deficit_mm"] = etc - eta
    out["deficit_acum_mm"] = out["deficit_mm"].cumsum()
    out["drenaje_mm"] = drain
    out["escurrimiento_mm"] = runoff
    out["balance_mm"] = out["precip_mm"] - out["etc_mm"]
    out["balance_acum_mm"] = out["balance_mm"].cumsum()
    out.attrs["kc_source"] = kc_source
    out.attrs["taw_mm"] = taw
    return out


def water_stress_summary(wb: pd.DataFrame, crop: Crop, sowing: date | None = None) -> dict:
    """Resume el estrés hídrico, ponderando la ventana crítica del cultivo."""
    if wb.empty:
        return {}
    n = len(wb)
    frac = np.linspace(0, 1, n) if sowing is None else _cycle_fraction(wb, crop, sowing)
    lo, hi = crop.critical_window
    in_crit = (frac >= lo) & (frac <= hi)

    etc_sum = float(wb["etc_mm"].sum())
    eta_sum = float(wb["eta_mm"].sum())
    etc_crit = float(wb.loc[in_crit, "etc_mm"].sum())
    eta_crit = float(wb.loc[in_crit, "eta_mm"].sum())

    rel_total = eta_sum / etc_sum if etc_sum else 1.0
    rel_crit = eta_crit / etc_crit if etc_crit else rel_total
    # Ecuación de respuesta al agua (FAO-56): la ventana crítica pesa doble
    penalty = crop.ky * ((1 - rel_crit) * 0.66 + (1 - rel_total) * 0.34)

    return {
        "dias_estres": int((wb["ks"] < 0.8).sum()),
        "dias_estres_severo": int((wb["ks"] < 0.5).sum()),
        "dias_estres_criticos": int((wb.loc[in_crit, "ks"] < 0.8).sum()),
        "deficit_total_mm": float(wb["deficit_mm"].sum()),
        "satisfaccion_hidrica": float(np.clip(rel_total, 0, 1)),
        "satisfaccion_critica": float(np.clip(rel_crit, 0, 1)),
        "penalidad_rinde": float(np.clip(penalty, 0, 0.95)),
        "agua_util_actual_pct": float(wb["agua_util_pct"].iloc[-1]),
        "drenaje_total_mm": float(wb["drenaje_mm"].sum()),
    }


def _cycle_fraction(wb: pd.DataFrame, crop: Crop, sowing: date) -> np.ndarray:
    days = np.array([(d - sowing).days for d in pd.to_datetime(wb["date"]).dt.date], dtype=float)
    return np.clip(days / max(1, crop.cycle_days), 0, 1.3)


# --------------------------------------------------------------------------
# Eventos y estreses térmicos
# --------------------------------------------------------------------------
def dry_spells(wx: pd.DataFrame, min_days: int = 10, threshold_mm: float = 1.0) -> pd.DataFrame:
    """Rachas secas: días consecutivos sin lluvia significativa.

    La duración se mide sobre el calendario, no contando filas: si la serie
    tiene un hueco, una racha de dos semanas no puede reportarse como de tres
    meses. Un salto de más de un día corta la racha.
    """
    d = wx.copy().reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    dry = pd.to_numeric(d["precip_mm"], errors="coerce").fillna(0) < threshold_mm
    gap = pd.Series(pd.to_datetime(d["date"])).diff().dt.days.fillna(1) > 1
    groups = ((dry != dry.shift()) | gap).cumsum()

    rows = []
    for _, g in d.groupby(groups):
        if not dry.loc[g.index[0]]:
            continue
        inicio, fin = g["date"].iloc[0], g["date"].iloc[-1]
        dias = (fin - inicio).days + 1
        if dias < min_days:
            continue
        rows.append({
            "inicio": inicio,
            "fin": fin,
            "dias": dias,
            "et0_acumulada_mm": float(pd.to_numeric(g["et0_mm"], errors="coerce").sum()),
        })
    return pd.DataFrame(rows)


def thermal_events(wx: pd.DataFrame, crop: Crop, sowing: date | None = None) -> pd.DataFrame:
    """Heladas y golpes de calor, marcando cuáles cayeron en período crítico."""
    d = wx.copy().reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    frac = _cycle_fraction(d, crop, sowing) if sowing else np.zeros(len(d))
    lo, hi = crop.critical_window
    rows = []
    for i, r in d.iterrows():
        crit = bool(lo <= frac[i] <= hi)
        if pd.notna(r["tmin"]) and r["tmin"] <= crop.frost_critical_c:
            rows.append({"date": r["date"], "tipo": "Helada", "valor": float(r["tmin"]),
                         "critico": crit,
                         "severidad": "critical" if (crit or r["tmin"] <= crop.frost_critical_c - 2)
                         else "warning"})
        if pd.notna(r["tmax"]) and r["tmax"] >= crop.heat_critical_c:
            rows.append({"date": r["date"], "tipo": "Golpe de calor", "valor": float(r["tmax"]),
                         "critico": crit,
                         "severidad": "critical" if crit else "warning"})
    return pd.DataFrame(rows)


def workability(wx: pd.DataFrame, lookahead: int = 7) -> pd.DataFrame:
    """Ventanas de piso: días aptos para entrar con la máquina.

    Regla simple y auditable: sin lluvia el día, menos de 10 mm en las 48 h
    previas y viento por debajo de 25 km/h para pulverizar.
    """
    d = wx.copy().reset_index(drop=True)
    p = pd.to_numeric(d["precip_mm"], errors="coerce").fillna(0)
    d["lluvia_48h_previas"] = p.rolling(3, min_periods=1).sum().shift(1).fillna(0)
    wind = pd.to_numeric(d.get("wind_kmh", pd.Series(np.nan, index=d.index)), errors="coerce")
    d["apto_piso"] = (p < 1) & (d["lluvia_48h_previas"] < 10)
    d["apto_pulverizar"] = d["apto_piso"] & (wind.fillna(0) < 25)
    return d[["date", "precip_mm", "lluvia_48h_previas", "apto_piso", "apto_pulverizar", "source"]]


# --------------------------------------------------------------------------
# Rendimiento orientativo
# --------------------------------------------------------------------------
def yield_estimate(indvi: float, crop: Crop, water_penalty: float = 0.0,
                   uniformity: float = 100.0) -> dict:
    """Estimación orientativa de rendimiento.

    Modelo deliberadamente simple y transparente: la integral del NDVI marca
    la biomasa acumulada, el balance hídrico penaliza por déficit en la
    ventana crítica y la heterogeneidad del lote castiga la media.

    NO reemplaza un aforo. La banda de confianza es amplia a propósito.
    """
    if crop.yield_ref_tha <= 0 or indvi <= 0:
        return {"estimado_tha": None, "rango_tha": None, "confianza": "sin dato"}

    biomass_ratio = float(np.clip(indvi / crop.indvi_ref, 0.2, 1.6))
    unif_factor = 0.90 + 0.10 * float(np.clip(uniformity, 0, 100)) / 100
    est = crop.yield_ref_tha * biomass_ratio * (1 - float(np.clip(water_penalty, 0, 0.9))) * unif_factor

    # La incertidumbre crece cuando el lote es heterogéneo o hubo estrés fuerte
    spread = 0.18 + 0.10 * water_penalty + 0.10 * (1 - unif_factor) * 10
    conf = "media" if spread < 0.28 else "baja"
    return {
        "estimado_tha": round(float(est), 2),
        "rango_tha": (round(float(est * (1 - spread)), 2), round(float(est * (1 + spread)), 2)),
        "confianza": conf,
        "biomasa_relativa": round(biomass_ratio, 2),
        "penalidad_hidrica": round(float(water_penalty), 3),
    }
