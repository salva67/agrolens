"""Orquestador del análisis.

La interfaz no calcula nada: pide un `AnalysisResult` y lo dibuja. El PDF usa
exactamente el mismo objeto, así que el informe nunca puede contradecir a la
pantalla.

El pipeline degrada con elegancia: si falla el clima, siguen los índices; si
falla el ráster, siguen las series; si no hay Earth Engine, entra el modo
demostración. Cada degradación queda registrada en `result.avisos`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from .analytics import (
    agronomy, alerts as alerts_mod, anomaly, phenology, storms as storms_mod, timeseries,
    zones as zones_mod,
)
from .config import SETTINGS
from .crops import Crop, get_crop
from .geo import area_ha, centroid_latlon
from .models import Alert, AnalysisConfig, Lote, Phenology
from .sources import demo

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


@dataclass
class AnalysisResult:
    lote: Lote
    config: AnalysisConfig
    crop: Crop

    # Satelital
    series: pd.DataFrame = field(default_factory=pd.DataFrame)
    curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    trend: dict = field(default_factory=dict)
    gaps: dict = field(default_factory=dict)
    fenologia: Phenology = field(default_factory=Phenology)
    raster: dict | None = None
    zonas: dict | None = None

    # Clima
    clima: pd.DataFrame = field(default_factory=pd.DataFrame)
    balance: pd.DataFrame = field(default_factory=pd.DataFrame)
    estres: dict = field(default_factory=dict)
    rachas_secas: pd.DataFrame = field(default_factory=pd.DataFrame)
    eventos_termicos: pd.DataFrame = field(default_factory=pd.DataFrame)
    piso: pd.DataFrame = field(default_factory=pd.DataFrame)
    resumen_clima: dict = field(default_factory=dict)

    # Tormentas
    tormentas: pd.DataFrame = field(default_factory=pd.DataFrame)
    dano_tormenta: pd.DataFrame = field(default_factory=pd.DataFrame)
    resumen_tormentas: dict = field(default_factory=dict)

    # Histórico
    historia: pd.DataFrame = field(default_factory=pd.DataFrame)
    banda: pd.DataFrame = field(default_factory=pd.DataFrame)
    ranking: pd.DataFrame = field(default_factory=pd.DataFrame)
    resumen_historia: dict = field(default_factory=dict)

    # Síntesis
    rendimiento: dict = field(default_factory=dict)
    alertas: list[Alert] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    modo_demo: bool = False
    generado: date = field(default_factory=date.today)

    # -- accesos rápidos para la UI ---------------------------------------
    @property
    def ultimo_valor(self) -> float | None:
        if self.curve.empty:
            return None
        return float(self.curve["smooth"].iloc[-1])

    @property
    def ultima_fecha(self) -> date | None:
        if self.series.empty:
            return None
        return self.series["date"].iloc[-1]

    @property
    def alertas_criticas(self) -> list[Alert]:
        return [a for a in self.alertas if a.severity in ("critical", "serious")]

    @property
    def uniformidad(self) -> float | None:
        """Última uniformidad medible (las fechas de baja cobertura no cuentan)."""
        if self.series.empty or "uniformity" not in self.series:
            return None
        validas = self.series["uniformity"].dropna()
        return float(validas.iloc[-1]) if not validas.empty else None

    def salud(self) -> tuple[int, str]:
        """Puntaje 0–100 del estado del lote, con su etiqueta.

        Combina cuatro señales que un asesor mira junto: nivel del índice,
        tendencia reciente, uniformidad y satisfacción hídrica. Es un resumen,
        no un diagnóstico: el detalle está en las alertas.
        """
        parts: list[float] = []
        if self.ultimo_valor is not None and self.crop.ndvi_peak:
            parts.append(min(1.0, self.ultimo_valor / self.crop.ndvi_peak) * 100)
        if self.trend:
            parts.append(float(min(100, max(0, 50 + self.trend.get("slope_week", 0) * 600))))
        if self.uniformidad is not None:
            parts.append(self.uniformidad)
        if self.estres:
            parts.append(float(self.estres.get("satisfaccion_hidrica", 1)) * 100)
        if self.resumen_historia:
            parts.append(float(self.resumen_historia.get("percentil_actual", 50)))
        score = int(round(sum(parts) / len(parts))) if parts else 0
        label = ("Muy bueno" if score >= 80 else "Bueno" if score >= 65
                 else "Regular" if score >= 45 else "Comprometido")
        return score, label


# --------------------------------------------------------------------------
def _satellite_module(demo_mode: bool):
    if demo_mode:
        return demo, True
    try:
        from .sources import gee

        gee.init()
        return gee, False
    except Exception as exc:
        log.warning("Earth Engine no disponible, se usa el modo demostración: %s", exc)
        return demo, True


def run(
    lote: Lote,
    config: AnalysisConfig,
    *,
    include_raster: bool = True,
    include_history: bool = True,
    include_weather: bool = True,
    demo_mode: bool | None = None,
    progress: Progress | None = None,
) -> AnalysisResult:
    """Ejecuta el análisis completo del lote."""
    crop = get_crop(lote.crop)
    res = AnalysisResult(lote=lote, config=config, crop=crop)
    step = _stepper(progress)

    demo_mode = SETTINGS.demo_mode if demo_mode is None else demo_mode
    sat, is_demo = _satellite_module(demo_mode)
    res.modo_demo = is_demo
    if is_demo and not demo_mode:
        res.avisos.append(
            "No se pudo conectar con Earth Engine: se muestran datos de demostración, "
            "verosímiles pero no reales."
        )

    # ---- 1. Serie del índice -------------------------------------------
    step(0.05, "Buscando imágenes Sentinel-2 sin nubes…")
    try:
        if is_demo:
            res.series = demo.index_series(lote.geometry, config.start, config.end,
                                           config.index, lote.crop, lote.sowing_date)
        else:
            res.series = sat.index_series(
                lote.geometry, config.start, config.end, config.index,
                max_cloud=config.cloud_pct, min_valid_fraction=config.min_valid_fraction,
            )
    except Exception as exc:
        res.avisos.append(f"No se pudieron obtener las series satelitales: {exc}")
        res.series = pd.DataFrame()

    if not res.series.empty:
        step(0.25, "Reconstruyendo la curva del cultivo…")
        res.curve = timeseries.build_curve(res.series, "mean", config.smoothing_days,
                                           config.start, config.end)
        res.trend = timeseries.trend(res.curve)
        res.gaps = timeseries.gap_report(res.series)
        res.fenologia = phenology.extract(res.curve)

    # ---- 2. Clima -------------------------------------------------------
    if include_weather:
        step(0.35, "Descargando la serie agroclimática…")
        lat, lon = lote.centroid if any(lote.centroid) else centroid_latlon(lote.geometry)
        try:
            if is_demo:
                res.clima = demo.weather(lat, lon, config.start, config.end + timedelta(days=10))
            else:
                from .sources import weather as wx_mod

                res.clima = wx_mod.timeline(lat, lon, config.start,
                                            config.end + timedelta(days=SETTINGS.forecast_days))
                res.resumen_clima = wx_mod.summarize(res.clima)
                # Las normales 1991–2020 son 30 años de datos: la primera vez para
                # cada punto tarda, después queda en caché por un mes.
                step(0.42, "Calculando normales climáticas 1991–2020 (sólo la primera vez)…")
                res.clima = wx_mod.attach_climatology(res.clima, lat, lon)
        except Exception as exc:
            res.avisos.append(f"No se pudo obtener el clima: {exc}")

    if not res.clima.empty:
        step(0.50, "Calculando grados-día y balance hídrico…")
        try:
            gdd = agronomy.growing_degree_days(res.clima, crop, lote.sowing_date)
            res.balance = agronomy.water_balance(
                gdd, crop, lote.sowing_date, lote.soil_awc_mm,
                ndvi_curve=res.curve if not res.curve.empty else None,
            )
            res.balance["gdd"] = gdd["gdd"].values
            res.balance["gdd_acum"] = gdd["gdd_acum"].values
            res.balance["etapa"] = gdd["etapa"].values
            res.estres = agronomy.water_stress_summary(res.balance, crop, lote.sowing_date)
            res.rachas_secas = agronomy.dry_spells(res.clima)
            res.eventos_termicos = agronomy.thermal_events(res.clima, crop, lote.sowing_date)
            res.piso = agronomy.workability(res.clima)
        except Exception as exc:
            res.avisos.append(f"No se pudo completar el balance agronómico: {exc}")

        # ---- Tormentas: exposición y daño observado ---------------------
        step(0.58, "Buscando tormentas y daño asociado…")
        try:
            res.tormentas = storms_mod.storm_days(res.clima)
            res.tormentas = storms_mod.critical_window_events(
                res.tormentas, crop, lote.sowing_date)
            res.resumen_tormentas = storms_mod.exposure_summary(res.tormentas, res.clima)
            if not res.series.empty:
                res.dano_tormenta = storms_mod.detect_damage(
                    res.series, res.tormentas, crop, lote.sowing_date)
        except Exception as exc:
            res.avisos.append(f"No se pudo analizar la exposición a tormentas: {exc}")

    # ---- 3. Ráster y zonas ---------------------------------------------
    if include_raster:
        step(0.65, "Descargando el ráster del lote…")
        try:
            if is_demo:
                base = res.curve["smooth"].iloc[-1] if not res.curve.empty else 0.65
                res.raster = demo.raster(lote.geometry, config.index, float(base))
            else:
                res.raster = sat.download_index_raster(
                    lote.geometry, max(config.start, config.end - timedelta(days=30)),
                    config.end, config.index, mode="composite",
                )
            step(0.75, "Delimitando ambientes…")
            res.zonas = zones_mod.management_zones(res.raster, config.n_zones)
        except Exception as exc:
            res.avisos.append(f"No se pudo generar el mapa intra-lote: {exc}")

    # ---- 4. Historia ----------------------------------------------------
    if include_history and lote.sowing_date and not is_demo:
        step(0.85, "Comparando contra campañas anteriores…")
        try:
            def fetch(geom, start, end, index_key):
                return sat.index_series(geom, start, end, index_key, max_cloud=config.cloud_pct)

            res.historia = anomaly.build_history(
                fetch, lote.geometry, lote.sowing_date, crop.cycle_days, config.index,
                config.history_years, config.smoothing_days,
            )
            res.banda = anomaly.envelope(res.historia)
            res.ranking = anomaly.rank_current(res.curve, lote.sowing_date, res.historia)
            res.resumen_historia = anomaly.summarize(res.ranking)
        except Exception as exc:
            res.avisos.append(f"No se pudo construir la comparación histórica: {exc}")

    # ---- 5. Síntesis ----------------------------------------------------
    step(0.95, "Generando hallazgos…")
    if not res.curve.empty and lote.sowing_date:
        indvi = timeseries.integral(res.curve, floor=0.15, start=lote.sowing_date)
        unif = res.uniformidad if res.uniformidad is not None else 100.0
        res.rendimiento = agronomy.yield_estimate(
            indvi, crop, res.estres.get("penalidad_rinde", 0.0), unif
        )

    res.alertas = alerts_mod.evaluate(
        crop=crop, series=res.series, curve=res.curve, trend=res.trend, weather=res.clima,
        balance=res.balance, stress=res.estres, thermal=res.eventos_termicos,
        spells=res.rachas_secas, history=res.resumen_historia, zones=res.zonas,
        gaps=res.gaps, storms=res.tormentas, damage=res.dano_tormenta,
        sowing=lote.sowing_date, index_key=config.index,
    )
    step(1.0, "Listo")
    return res


def _stepper(progress: Progress | None) -> Progress:
    def _noop(_pct: float, _msg: str) -> None:
        return None

    return progress or _noop


def quick_summary(res: AnalysisResult) -> dict[str, Any]:
    """Cifras de portada, ya formateadas."""
    score, label = res.salud()
    out: dict[str, Any] = {
        "superficie_ha": round(area_ha(res.lote.geometry), 1),
        "salud": score,
        "salud_label": label,
        "indice": res.config.index,
        "valor_actual": round(res.ultimo_valor, 3) if res.ultimo_valor is not None else None,
        "fecha_ultima_imagen": res.ultima_fecha,
        "observaciones": int(len(res.series)),
        "tendencia_semanal": round(res.trend.get("slope_week", 0.0), 4) if res.trend else None,
        "uniformidad": round(res.uniformidad, 0) if res.uniformidad is not None else None,
        "alertas": len(res.alertas),
        "alertas_criticas": len(res.alertas_criticas),
    }
    out.update({k: v for k, v in (res.resumen_clima or {}).items()})
    if res.estres:
        out["agua_util_pct"] = round(res.estres.get("agua_util_actual_pct", 0), 0)
    if res.rendimiento.get("estimado_tha"):
        out["rinde_estimado_tha"] = res.rendimiento["estimado_tha"]
    if res.resumen_historia:
        out["percentil_historico"] = res.resumen_historia.get("percentil_actual")
    return out
