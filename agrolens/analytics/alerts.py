"""Motor de alertas.

Reglas explícitas y auditables sobre los resultados del análisis. Cada regla
declara qué miró, con qué umbral y qué hacer. Un informe que dice "NDVI 0,58"
no sirve; uno que dice "el vigor cayó 0,08 en dos semanas mientras el resto
del lote se mantuvo, y hay 12 días de racha seca: recorrer el sector oeste"
sí.

Las reglas viven todas acá para poder revisarlas de un vistazo y ajustarlas
sin tocar la interfaz.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..crops import Crop
from ..models import Alert


def evaluate(
    *,
    crop: Crop,
    series: pd.DataFrame | None = None,
    curve: pd.DataFrame | None = None,
    trend: dict | None = None,
    weather: pd.DataFrame | None = None,
    balance: pd.DataFrame | None = None,
    stress: dict | None = None,
    thermal: pd.DataFrame | None = None,
    spells: pd.DataFrame | None = None,
    history: dict | None = None,
    zones: dict | None = None,
    gaps: dict | None = None,
    storms: pd.DataFrame | None = None,
    damage: pd.DataFrame | None = None,
    sowing: date | None = None,
    index_key: str = "NDVI",
) -> list[Alert]:
    """Corre todas las reglas y devuelve los hallazgos ordenados por severidad."""
    out: list[Alert] = []
    for rule in (
        _rule_trend, _rule_uniformity, _rule_history, _rule_water_stress,
        _rule_dry_spell, _rule_thermal, _rule_waterlogging, _rule_zones,
        _rule_coverage, _rule_forecast, _rule_stage,
        _rule_storm_damage, _rule_storm_exposure, _rule_storm_forecast,
    ):
        try:
            out.extend(rule(
                crop=crop, series=series, curve=curve, trend=trend, weather=weather,
                balance=balance, stress=stress, thermal=thermal, spells=spells,
                history=history, zones=zones, gaps=gaps, sowing=sowing, index_key=index_key,
                storms=storms, damage=damage,
            ))
        except Exception:  # una regla rota no puede tumbar el informe entero
            continue
    return sorted(out, key=lambda a: (a.rank, a.title))


# --------------------------------------------------------------------------
# Reglas satelitales
# --------------------------------------------------------------------------
def _rule_trend(*, trend, index_key, curve, **_) -> list[Alert]:
    if not trend or curve is None or curve.empty:
        return []
    slope = trend.get("slope_week", 0.0)
    if slope <= -0.04:
        sev = "critical" if slope <= -0.08 else "serious"
        return [Alert(
            code="VEG_CAIDA", severity=sev, source="satelital",
            title=f"Caída marcada del {index_key}",
            detail=f"El índice retrocede {abs(slope):.3f} por semana en las últimas 3 semanas "
                   f"(valor actual {curve['smooth'].iloc[-1]:.2f}).",
            recommendation="Recorrer el lote antes de 72 h. Descartar plaga, enfermedad, "
                           "deficiencia de nitrógeno o falta de agua según la etapa.",
            value=slope,
        )]
    if slope >= 0.05:
        return [Alert(
            code="VEG_CRECE", severity="good", source="satelital",
            title="Crecimiento activo",
            detail=f"El índice sube {slope:.3f} por semana: el cultivo está en plena expansión foliar.",
            recommendation="Momento oportuno para monitoreo de plagas de hoja y ajuste de fertilización.",
            value=slope,
        )]
    return []


def _rule_uniformity(*, series, index_key, **_) -> list[Alert]:
    if series is None or series.empty:
        return []
    # Sólo las fechas con cobertura suficiente dicen algo sobre uniformidad:
    # con el lote en senescencia o en barbecho, el CV siempre se dispara.
    validas = series[series["uniformity"].notna()] if "uniformity" in series else series
    if validas.empty:
        return []
    last = validas.iloc[-1]
    cv = float(last.get("cv", 0.0))
    if cv >= 0.22:
        sev = "serious" if cv >= 0.30 else "warning"
        return [Alert(
            code="VEG_HETEROGENEO", severity=sev, source="satelital",
            title="Lote heterogéneo",
            detail=f"El coeficiente de variación del {index_key} es {cv * 100:.0f} % "
                   f"(rango p10–p90: {last['p10']:.2f}–{last['p90']:.2f}).",
            recommendation="Revisar la zonificación: si el patrón se repite entre campañas es "
                           "estructural (suelo, drenaje) y justifica manejo por ambientes.",
            value=cv,
        )]
    if cv <= 0.10:
        return [Alert(
            code="VEG_UNIFORME", severity="good", source="satelital",
            title="Lote uniforme",
            detail=f"Variación interna de sólo {cv * 100:.0f} %: implantación y ambiente parejos.",
            recommendation="El promedio del lote es representativo; el muestreo puede ser más liviano.",
            value=cv,
        )]
    return []


def _rule_history(*, history, index_key, **_) -> list[Alert]:
    if not history or "percentil_actual" not in history:
        return []
    pct = history["percentil_actual"]
    n = history.get("campañas_comparadas", 0)
    if pct < 15:
        return [Alert(
            code="HIST_BAJO", severity="serious", source="satelital",
            title="Campaña muy por debajo de la historia del lote",
            detail=f"El {index_key} está en el percentil {pct:.0f} respecto de las últimas "
                   f"{n} campañas a la misma altura del ciclo.",
            recommendation="Comparar con lotes vecinos del mismo cultivo: si también están bajos, "
                           "la causa es climática; si no, es de manejo o de lote.",
            value=pct,
        )]
    if pct < 35:
        return [Alert(
            code="HIST_BAJO_LEVE", severity="warning", source="satelital",
            title="Por debajo de la historia del lote",
            detail=f"Percentil {pct:.0f} contra {n} campañas anteriores.",
            recommendation="Seguir la evolución semanal antes de decidir una intervención.",
            value=pct,
        )]
    if pct >= 85:
        return [Alert(
            code="HIST_ALTO", severity="good", source="satelital",
            title="Campaña por encima de la historia del lote",
            detail=f"Percentil {pct:.0f} contra {n} campañas anteriores.",
            recommendation="Buen escenario para sostener la inversión en protección del rendimiento.",
            value=pct,
        )]
    return []


def _rule_zones(*, zones, **_) -> list[Alert]:
    if not zones or not zones.get("stats"):
        return []
    stats = zones["stats"]
    worst = min(stats, key=lambda s: s.mean if np.isfinite(s.mean) else 9e9)
    best = max(stats, key=lambda s: s.mean if np.isfinite(s.mean) else -9e9)
    if not np.isfinite(worst.mean) or not np.isfinite(best.mean):
        return []
    gap = best.mean - worst.mean
    if gap >= 0.15 and worst.pct >= 10:
        return [Alert(
            code="ZONA_BRECHA", severity="warning", source="satelital",
            title="Brecha importante entre ambientes",
            detail=f"La zona de menor vigor ocupa {worst.pct:.0f} % del lote ({worst.area_ha:.1f} ha) "
                   f"y está {gap:.2f} unidades por debajo de la mejor.",
            recommendation="Muestrear suelo en la zona baja antes de la próxima siembra y evaluar "
                           "dosis variable o cambio de ambiente.",
            value=gap,
        )]
    return []


def _rule_coverage(*, gaps, **_) -> list[Alert]:
    if not gaps:
        return []
    if gaps.get("gap_max_dias", 0) >= 25:
        return [Alert(
            code="DATO_HUECO", severity="info", source="satelital",
            title="Hueco largo en el seguimiento satelital",
            detail=f"Hubo hasta {gaps['gap_max_dias']} días sin imagen útil "
                   f"({gaps.get('n_obs', 0)} observaciones válidas en el período).",
            recommendation="Los valores interpolados en ese tramo son estimaciones: no basar "
                           "decisiones puntuales en esa ventana.",
            value=float(gaps["gap_max_dias"]),
        )]
    return []


# --------------------------------------------------------------------------
# Reglas climáticas y agronómicas
# --------------------------------------------------------------------------
def _rule_water_stress(*, stress, crop, **_) -> list[Alert]:
    if not stress:
        return []
    out = []
    aw = stress.get("agua_util_actual_pct", 100)
    if aw < 30:
        out.append(Alert(
            code="AGUA_BAJA", severity="critical" if aw < 15 else "serious", source="agronomía",
            title="Reserva de agua útil en niveles bajos",
            detail=f"El perfil está al {aw:.0f} % de su capacidad de agua útil.",
            recommendation="Con este nivel, cualquier semana sin lluvia se traduce en pérdida de "
                           "rendimiento. Priorizar riego si está disponible.",
            value=aw,
        ))
    dias_crit = stress.get("dias_estres_criticos", 0)
    if dias_crit >= 5:
        out.append(Alert(
            code="ESTRES_CRITICO", severity="critical" if dias_crit >= 12 else "serious",
            source="agronomía",
            title="Estrés hídrico en el período crítico",
            detail=f"{dias_crit} días con estrés durante la ventana crítica de {crop.label.lower()}. "
                   f"Satisfacción hídrica en ese tramo: {stress.get('satisfaccion_critica', 1) * 100:.0f} %.",
            recommendation="Es el estrés que más pesa en el rinde. Ajustar la expectativa de "
                           "cosecha y revisar la estrategia de protección.",
            value=float(dias_crit),
        ))
    return out


def _rule_dry_spell(*, spells, weather, **_) -> list[Alert]:
    if spells is None or spells.empty:
        return []
    last = spells.iloc[-1]
    dias = int(last["dias"])
    if dias >= 20:
        sev = "serious"
    elif dias >= 12:
        sev = "warning"
    else:
        return []
    return [Alert(
        code="RACHA_SECA", severity=sev, source="clima",
        title=f"Racha seca de {dias} días",
        detail=f"Entre el {pd.Timestamp(last['inicio']):%d/%m} y el {pd.Timestamp(last['fin']):%d/%m} "
               f"no hubo lluvias significativas; la demanda atmosférica acumuló "
               f"{last['et0_acumulada_mm']:.0f} mm.",
        recommendation="Contrastar con la reserva de agua útil: el impacto depende de cuánto "
                       "había en el perfil al empezar la racha.",
        value=float(dias),
    )]


def _rule_thermal(*, thermal, crop, **_) -> list[Alert]:
    if thermal is None or thermal.empty:
        return []
    out = []
    frost = thermal[thermal["tipo"] == "Helada"]
    heat = thermal[thermal["tipo"] == "Golpe de calor"]
    if not frost.empty:
        crit = frost["critico"].any()
        out.append(Alert(
            code="HELADA", severity="critical" if crit else "warning", source="clima",
            title=f"{len(frost)} evento(s) de helada",
            detail=f"Mínima más baja: {frost['valor'].min():.1f} °C el "
                   f"{pd.Timestamp(frost.loc[frost['valor'].idxmin(), 'date']):%d/%m}."
                   + (" Al menos una cayó en período crítico." if crit else ""),
            recommendation="Evaluar daño a campo en las 72 h siguientes: el satélite recién lo "
                           "muestra cuando el tejido se seca.",
            value=float(frost["valor"].min()),
        ))
    if not heat.empty:
        crit = heat["critico"].any()
        out.append(Alert(
            code="CALOR", severity="serious" if crit else "warning", source="clima",
            title=f"{len(heat)} día(s) de golpe de calor",
            detail=f"Máxima más alta: {heat['valor'].max():.1f} °C "
                   f"(umbral de daño para {crop.label.lower()}: {crop.heat_critical_c:.0f} °C)."
                   + (" Coincidió con el período crítico." if crit else ""),
            recommendation="Si coincidió con floración, esperar menor cuaje. Ajustar la "
                           "expectativa de rinde y revisar el aborto de flores a campo.",
            value=float(heat["valor"].max()),
        ))
    return out


def _rule_waterlogging(*, balance, weather, **_) -> list[Alert]:
    if balance is None or balance.empty:
        return []
    recent = balance.tail(21)
    drenaje = float(recent["drenaje_mm"].sum())
    lluvia = float(recent["precip_mm"].sum())
    if drenaje > 60 and lluvia > 120:
        return [Alert(
            code="EXCESO_AGUA", severity="warning", source="agronomía",
            title="Exceso hídrico reciente",
            detail=f"En las últimas 3 semanas llovieron {lluvia:.0f} mm y el perfil drenó "
                   f"{drenaje:.0f} mm por encima de su capacidad.",
            recommendation="Revisar bajos y zonas de anegamiento con el índice NDWI: pueden "
                           "haber perdido plantas y conviene descontarlos de la superficie útil.",
            value=drenaje,
        )]
    return []


def _rule_forecast(*, weather, **_) -> list[Alert]:
    if weather is None or weather.empty or "source" not in weather:
        return []
    fc = weather[weather["source"] == "pronóstico"]
    if fc.empty:
        return []
    lluvia = float(fc["precip_mm"].sum())
    dias = len(fc)
    if lluvia >= 40:
        return [Alert(
            code="PRON_LLUVIA", severity="info", source="clima",
            title=f"Se esperan {lluvia:.0f} mm en los próximos {dias} días",
            detail="El pronóstico acumula lluvias relevantes para la recarga del perfil.",
            recommendation="Adelantar las labores que requieran piso firme antes del evento.",
            value=lluvia,
        )]
    if lluvia < 5 and dias >= 10:
        return [Alert(
            code="PRON_SECO", severity="warning", source="clima",
            title=f"Sin lluvias relevantes en los próximos {dias} días",
            detail=f"El pronóstico acumula apenas {lluvia:.0f} mm.",
            recommendation="Proyectar la reserva de agua útil hacia adelante antes de comprometer "
                           "insumos de alto costo.",
            value=lluvia,
        )]
    return []


def _rule_stage(*, balance, crop, sowing, **_) -> list[Alert]:
    if balance is None or balance.empty or sowing is None or "gdd_acum" not in balance:
        return []
    gdd = float(balance["gdd_acum"].iloc[-1])
    stage = crop.stage_at(gdd)
    if not stage:
        return []
    frac = gdd / crop.gdd_cycle if crop.gdd_cycle else 0
    lo, hi = crop.critical_window
    if lo <= frac <= hi:
        return [Alert(
            code="ETAPA_CRITICA", severity="info", source="agronomía",
            title=f"El cultivo está en período crítico ({stage.name})",
            detail=f"Suma térmica acumulada: {gdd:.0f} °C día ({frac * 100:.0f} % del ciclo).",
            recommendation="Es la ventana donde el estrés define el rinde: sostener el monitoreo "
                           "semanal y no postergar decisiones de protección.",
            value=gdd,
        )]
    return []


# --------------------------------------------------------------------------
# Reglas de tormenta
# --------------------------------------------------------------------------
def _rule_storm_damage(*, damage, index_key, **_) -> list[Alert]:
    """Lo más accionable de todo el informe: el satélite vio caer el lote
    justo cuando pasó una tormenta."""
    if damage is None or damage.empty:
        return []
    out = []
    orden = {"alta": 0, "media": 1, "baja": 2}
    for _, d in damage.sort_values("confianza", key=lambda s: s.map(orden)).head(3).iterrows():
        sev = {"alta": "critical", "media": "serious"}.get(d["confianza"], "warning")
        que = "granizo" if d["granizo"] else "tormenta"
        out.append(Alert(
            code="DANO_TORMENTA", severity=sev, source="satelital",
            title=f"Posible daño por {que} del {pd.Timestamp(d['tormenta']):%d/%m}",
            detail=(f"El {index_key} cayó {d['caida']:.2f} entre el "
                    f"{pd.Timestamp(d['fecha_antes']):%d/%m} y el "
                    f"{pd.Timestamp(d['fecha_despues']):%d/%m} "
                    f"({d['valor_antes']:.2f} → {d['valor_despues']:.2f}, {d['dias']} días). "
                    f"En esa ventana se registró: {d['detalle']}. "
                    f"Confianza de la atribución: {d['confianza']}."),
            recommendation=("Recorrer el lote y documentar con fotos fechadas. Si hay seguro, "
                            "avisar ahora: la fecha del evento y la caída del índice son "
                            "respaldo objetivo del reclamo."),
            value=float(d["caida"]),
        ))
    return out


def _rule_storm_exposure(*, storms, crop, **_) -> list[Alert]:
    if storms is None or storms.empty:
        return []
    obs = storms[storms["source"] != "pronóstico"] if "source" in storms else storms
    if obs.empty:
        return []

    out = []
    granizo = obs[obs["granizo"]]
    if not granizo.empty:
        criticos = granizo[granizo.get("en_periodo_critico", False)] \
            if "en_periodo_critico" in granizo else granizo.iloc[0:0]
        ult = granizo.iloc[-1]
        out.append(Alert(
            code="GRANIZO", severity="serious" if not criticos.empty else "warning",
            source="clima",
            title=f"{len(granizo)} día(s) con granizo declarado sobre el lote",
            detail=(f"El último, el {pd.Timestamp(ult['date']):%d/%m/%Y} ({ult['tipo']})."
                    + (f" {len(criticos)} cayeron en el período crítico de "
                       f"{crop.label.lower()}." if not criticos.empty else "")),
            recommendation=("Verificar a campo. El registro meteorológico indica granizo en la "
                            "zona, no necesariamente sobre este lote: para dimensionarlo, "
                            "usar el análisis GOES de esa fecha."),
            value=float(len(granizo)),
        ))

    rafagas = pd.to_numeric(obs["rafaga_kmh"], errors="coerce").dropna()
    if not rafagas.empty and rafagas.max() >= 80:
        peor = obs.loc[rafagas.idxmax()]
        out.append(Alert(
            code="VIENTO", severity="serious" if rafagas.max() >= 100 else "warning",
            source="clima",
            title=f"Ráfagas de hasta {rafagas.max():.0f} km/h",
            detail=(f"La máxima se registró el {pd.Timestamp(peor['date']):%d/%m/%Y}. "
                    f"{int((rafagas >= 80).sum())} día(s) superaron los 80 km/h."),
            recommendation=("Con el cultivo en llenado o cerca de cosecha, revisar vuelco y "
                            "desgrane: son pérdidas que no se ven desde el satélite hasta "
                            "que el lote ya se secó."),
            value=float(rafagas.max()),
        ))
    return out


def _rule_storm_forecast(*, storms, **_) -> list[Alert]:
    if storms is None or storms.empty or "source" not in storms:
        return []
    fc = storms[storms["source"] == "pronóstico"]
    if fc.empty:
        return []
    peor = fc.loc[fc["severidad"].idxmax()]
    granizo = bool(fc["granizo"].any())
    if peor["severidad"] < 30 and not granizo:
        return []
    return [Alert(
        code="PRON_TORMENTA", severity="warning" if granizo else "info", source="clima",
        title=f"Tormenta pronosticada para el {pd.Timestamp(peor['date']):%d/%m}",
        detail=f"{peor['tipo']}." + (" El modelo anticipa granizo." if granizo else ""),
        recommendation=("Adelantar labores que requieran piso firme y, si hay cultivo en "
                        "condiciones de cosecha, evaluar entrar antes del evento."),
        value=float(peor["severidad"]),
    )]


def to_dataframe(alerts: list[Alert]) -> pd.DataFrame:
    from ..models import SEVERITY_LABEL

    return pd.DataFrame([{
        "Severidad": SEVERITY_LABEL.get(a.severity, a.severity),
        "Origen": a.source.capitalize(),
        "Hallazgo": a.title,
        "Detalle": a.detail,
        "Recomendación": a.recommendation,
    } for a in alerts])
