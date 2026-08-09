"""Tormentas: exposición a granizo y viento, y detección de daño.

Tres capas, de menor a mayor valor:

1. **Exposición** — qué le pasó al lote según el registro meteorológico:
   ráfagas, tormentas, granizo declarado por el código WMO.
2. **Severidad** — cuánto de eso pudo hacer daño, ponderado por la etapa del
   cultivo: 80 km/h en un trigo espigado no es lo mismo que en un trigo en
   macollaje.
3. **Daño observado** — el cruce que ningún dato climático da solo: una caída
   brusca del índice de vegetación que coincide en el tiempo con una tormenta.
   El clima dice que *pudo* pasar; el satélite dice que *pasó*.

Nada de esto reemplaza al perito. Sirve para saber a qué lote ir primero y con
qué fecha en la mano.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..crops import Crop

# --------------------------------------------------------------------------
# Códigos WMO relevantes (los que devuelve Open-Meteo en `weather_code`)
# --------------------------------------------------------------------------
WMO_TORMENTA = 95          # tormenta, débil o moderada
WMO_GRANIZO_LEVE = 96      # tormenta con granizo leve
WMO_GRANIZO_FUERTE = 99    # tormenta con granizo fuerte
WMO_CHAPARRONES = (80, 81, 82)

WMO_LABEL = {
    95: "Tormenta eléctrica",
    96: "Tormenta con granizo",
    99: "Tormenta con granizo fuerte",
    80: "Chaparrones aislados",
    81: "Chaparrones moderados",
    82: "Chaparrones violentos",
}

# Umbrales de ráfaga (km/h). El daño mecánico en cultivos extensivos empieza a
# ser relevante cerca de los 60; por encima de 90 hay vuelco en cereales con
# espiga y desgrane en girasol y soja avanzada.
RAFAGA_ATENCION = 60.0
RAFAGA_DANO = 80.0
RAFAGA_SEVERA = 100.0

# Lluvia intensa en pocas horas: proxy de tormenta convectiva con posible
# anegamiento o erosión, distinto de una lluvia pareja del mismo total.
INTENSIDAD_MM_HORA = 8.0


def _wmo(v) -> int | None:
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 1. Eventos de tormenta a partir del registro meteorológico
# --------------------------------------------------------------------------
def storm_days(wx: pd.DataFrame) -> pd.DataFrame:
    """Días con algún indicio de tormenta, con su causa y severidad.

    Devuelve una fila por día, con las columnas `granizo` (declarado por el
    modelo), `rafaga_kmh`, `intensidad_mm_h` y una severidad 0–100.
    """
    if wx is None or wx.empty:
        return pd.DataFrame(columns=["date", "tipo", "severidad", "granizo", "rafaga_kmh",
                                     "lluvia_mm", "intensidad_mm_h", "wmo", "source"])

    d = wx.copy().reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    gust = pd.to_numeric(d.get("gust_kmh", pd.Series(np.nan, index=d.index)), errors="coerce")
    lluvia = pd.to_numeric(d.get("precip_mm", pd.Series(0.0, index=d.index)), errors="coerce").fillna(0)
    horas = pd.to_numeric(d.get("precip_horas", pd.Series(np.nan, index=d.index)), errors="coerce")
    intensidad = lluvia / horas.replace(0, np.nan)

    filas = []
    for i, r in d.iterrows():
        codigo = _wmo(r.get("wmo"))
        g = float(gust.iloc[i]) if pd.notna(gust.iloc[i]) else np.nan
        inten = float(intensidad.iloc[i]) if pd.notna(intensidad.iloc[i]) else np.nan
        granizo = codigo in (WMO_GRANIZO_LEVE, WMO_GRANIZO_FUERTE)

        motivos = []
        if granizo:
            motivos.append(WMO_LABEL.get(codigo, "Granizo"))
        elif codigo == WMO_TORMENTA:
            motivos.append("Tormenta eléctrica")
        elif codigo in WMO_CHAPARRONES and (np.isnan(inten) or inten >= INTENSIDAD_MM_HORA):
            motivos.append(WMO_LABEL.get(codigo, "Chaparrones"))
        if not np.isnan(g) and g >= RAFAGA_ATENCION:
            motivos.append(f"Ráfagas de {g:.0f} km/h")
        if not np.isnan(inten) and inten >= INTENSIDAD_MM_HORA:
            motivos.append(f"Lluvia intensa ({inten:.0f} mm/h)")

        if not motivos:
            continue

        filas.append({
            "date": r["date"],
            "tipo": " · ".join(motivos),
            "severidad": _severidad(codigo, g, inten, float(lluvia.iloc[i])),
            "granizo": granizo,
            "rafaga_kmh": None if np.isnan(g) else round(g, 1),
            "lluvia_mm": round(float(lluvia.iloc[i]), 1),
            "intensidad_mm_h": None if np.isnan(inten) else round(inten, 1),
            "wmo": codigo,
            "source": r.get("source", ""),
        })

    out = pd.DataFrame(filas)
    return out.sort_values("date").reset_index(drop=True) if not out.empty else out


def _severidad(codigo: int | None, rafaga: float, intensidad: float, lluvia: float) -> int:
    """0–100 combinando granizo declarado, ráfaga y intensidad de lluvia."""
    s = 0.0
    if codigo == WMO_GRANIZO_FUERTE:
        s += 60
    elif codigo == WMO_GRANIZO_LEVE:
        s += 40
    elif codigo == WMO_TORMENTA:
        s += 20
    if not np.isnan(rafaga):
        s += float(np.clip((rafaga - RAFAGA_ATENCION) / (RAFAGA_SEVERA - RAFAGA_ATENCION), 0, 1)) * 35
    if not np.isnan(intensidad):
        s += float(np.clip((intensidad - INTENSIDAD_MM_HORA) / 20.0, 0, 1)) * 15
    if lluvia >= 50:
        s += 5
    # Si el día llegó a listarse es porque cruzó algún umbral: mostrar
    # "severidad 0" al lado de un evento haría dudar de la tabla entera.
    return int(np.clip(round(s), 5, 100))


# --------------------------------------------------------------------------
# 2. Exposición de la campaña
# --------------------------------------------------------------------------
def exposure_summary(storms: pd.DataFrame, wx: pd.DataFrame | None = None) -> dict:
    """Resumen de exposición del período, separando observado de pronóstico."""
    if storms is None or storms.empty:
        base = {"eventos": 0, "con_granizo": 0, "rafaga_max_kmh": None,
                "dias_rafaga_dano": 0, "severidad_max": 0, "eventos_pronosticados": 0}
        if wx is not None and not wx.empty and "gust_kmh" in wx:
            g = pd.to_numeric(wx["gust_kmh"], errors="coerce")
            base["rafaga_max_kmh"] = None if g.dropna().empty else round(float(g.max()), 1)
        return base

    obs = storms[storms["source"] != "pronóstico"] if "source" in storms else storms
    fc = storms[storms["source"] == "pronóstico"] if "source" in storms else storms.iloc[0:0]
    rafagas = pd.to_numeric(obs["rafaga_kmh"], errors="coerce").dropna()
    return {
        "eventos": int(len(obs)),
        "con_granizo": int(obs["granizo"].sum()),
        "rafaga_max_kmh": round(float(rafagas.max()), 1) if not rafagas.empty else None,
        "dias_rafaga_dano": int((rafagas >= RAFAGA_DANO).sum()),
        "severidad_max": int(obs["severidad"].max()) if not obs.empty else 0,
        "eventos_pronosticados": int(len(fc)),
    }


def critical_window_events(storms: pd.DataFrame, crop: Crop, sowing: date | None) -> pd.DataFrame:
    """Marca qué eventos cayeron en el período crítico del cultivo.

    Es la diferencia entre un susto y una pérdida: el mismo granizo antes de
    floración se compensa con rebrote, y durante el llenado no.
    """
    if storms is None or storms.empty:
        return storms
    out = storms.copy()
    if sowing is None or not crop.cycle_days:
        out["en_periodo_critico"] = False
        return out
    lo, hi = crop.critical_window
    frac = np.array([(d - sowing).days / crop.cycle_days for d in out["date"]], dtype=float)
    out["dias_desde_siembra"] = [(d - sowing).days for d in out["date"]]
    out["en_periodo_critico"] = (frac >= lo) & (frac <= hi)
    out["en_ciclo"] = (frac >= 0) & (frac <= 1.05)
    return out


# --------------------------------------------------------------------------
# 3. Daño observado: caída brusca del índice junto a una tormenta
# --------------------------------------------------------------------------
def detect_damage(
    series: pd.DataFrame,
    storms: pd.DataFrame,
    crop: Crop,
    sowing: date | None = None,
    min_drop: float = 0.08,
    max_gap_days: int = 16,
    window_days: int = 3,
) -> pd.DataFrame:
    """Busca caídas abruptas del índice que coincidan con una tormenta.

    Compara observaciones satelitales consecutivas. Una caída fuerte entre dos
    fechas cercanas, con una tormenta en el medio, es la firma de daño físico:
    el granizo y el viento destruyen tejido verde de un día para el otro,
    mientras que la seca y las enfermedades bajan el índice de a poco.

    Se descartan las caídas de fin de ciclo, donde perder verde es lo esperable
    (senescencia y cosecha), para no llenar el informe de falsos positivos.
    """
    vacio = pd.DataFrame(columns=["fecha_antes", "fecha_despues", "caida", "valor_antes",
                                  "valor_despues", "dias", "tormenta", "severidad",
                                  "granizo", "confianza", "detalle"])
    if series is None or series.empty or len(series) < 2:
        return vacio
    if storms is None or storms.empty:
        return vacio

    s = series.sort_values("date").reset_index(drop=True)
    eventos = {}
    for _, ev in storms.iterrows():
        eventos[ev["date"]] = ev

    filas = []
    for i in range(1, len(s)):
        antes, despues = s.iloc[i - 1], s.iloc[i]
        dias = (despues["date"] - antes["date"]).days
        if dias <= 0 or dias > max_gap_days:
            continue
        caida = float(antes["mean"]) - float(despues["mean"])
        if caida < min_drop:
            continue

        # ¿El cultivo estaba en una etapa donde perder verde es anormal?
        if sowing is not None and crop.cycle_days:
            frac = (despues["date"] - sowing).days / crop.cycle_days
            if frac > 0.88 or frac < 0:
                continue  # madurez o cosecha: la caída es esperable
            if frac > 0.80 and caida < 0.15:
                continue  # senescencia temprana: sólo caídas muy grandes

        # Tormentas dentro de la ventana entre ambas observaciones
        candidatas = [
            ev for d, ev in eventos.items()
            if antes["date"] - timedelta(days=window_days) <= d <= despues["date"]
        ]
        if not candidatas:
            continue
        peor = max(candidatas, key=lambda e: e["severidad"])

        filas.append({
            "fecha_antes": antes["date"],
            "fecha_despues": despues["date"],
            "valor_antes": round(float(antes["mean"]), 3),
            "valor_despues": round(float(despues["mean"]), 3),
            "caida": round(caida, 3),
            "dias": int(dias),
            "tormenta": peor["date"],
            "severidad": int(peor["severidad"]),
            "granizo": bool(peor["granizo"]),
            "confianza": _confianza(caida, int(dias), peor),
            "detalle": peor["tipo"],
        })

    return pd.DataFrame(filas) if filas else vacio


def _confianza(caida: float, dias: int, evento) -> str:
    """Cuán atribuible es la caída a la tormenta.

    Alta pide las tres cosas: caída grande, ventana corta entre imágenes y una
    tormenta severa. Con una ventana de dos semanas entre imágenes, cualquier
    otra cosa pudo pasar en el medio.
    """
    puntos = 0
    puntos += 2 if caida >= 0.15 else 1 if caida >= 0.10 else 0
    puntos += 2 if dias <= 6 else 1 if dias <= 10 else 0
    puntos += 2 if evento["severidad"] >= 60 else 1 if evento["severidad"] >= 35 else 0
    puntos += 1 if evento["granizo"] else 0
    if puntos >= 6:
        return "alta"
    if puntos >= 4:
        return "media"
    return "baja"


def damage_area_estimate(raster_before: dict | None, raster_after: dict | None,
                         drop_threshold: float = 0.10) -> dict | None:
    """Superficie del lote con caída del índice por encima del umbral.

    Requiere dos rásteres de la misma grilla (antes y después del evento).
    Es una estimación de extensión, no de porcentaje de pérdida de rinde.
    """
    if not raster_before or not raster_after:
        return None
    a = np.asarray(raster_before["values"], dtype="float32")
    b = np.asarray(raster_after["values"], dtype="float32")
    if a.shape != b.shape:
        return None
    diff = a - b
    valido = np.isfinite(diff)
    if not valido.any():
        return None
    afectado = valido & (diff >= drop_threshold)

    from .zones import _pixel_area_ha

    px = _pixel_area_ha(raster_before)
    return {
        "values": np.where(valido, diff, np.nan),
        "transform": raster_before["transform"],
        "crs": raster_before["crs"],
        "index": "Δ índice",
        "area_afectada_ha": round(float(afectado.sum() * px), 2),
        "pct_afectado": round(100 * float(afectado.sum()) / float(valido.sum()), 1),
        "caida_media": round(float(np.nanmean(diff[afectado])), 3) if afectado.any() else 0.0,
        "caida_max": round(float(np.nanmax(diff[valido])), 3),
    }
