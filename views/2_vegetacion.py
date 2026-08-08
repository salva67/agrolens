"""Vegetación: curva del índice, mapa satelital, comparación entre fechas e índices."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import streamlit as st

from agrolens.analytics import phenology, timeseries
from agrolens.indices import FAMILY_LABELS, INDICES, MAP_INDEX_ORDER, get_index
from agrolens.ui import components as ui
from agrolens.viz import charts, maps

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote))
idx = get_index(cfg.index)

ui.hero("Vegetación", f"{lote.display} · {idx.label}")
res = ui.get_result(lote, cfg, raster=True, history=False, weather=False)
ui.data_source_badge(res)

if res.series.empty:
    st.warning("No hay observaciones satelitales válidas en el período elegido.")
    st.stop()

p = ui.palette()
dark = ui.is_dark()

# --------------------------------------------------------------------------
tab_curva, tab_mapa, tab_indices, tab_calidad = st.tabs(
    ["Curva del cultivo", "Mapa satelital", "Comparar índices", "Calidad del dato"]
)

# --------------------------------------------------------------------------
with tab_curva:
    ultimo = res.series.iloc[-1]
    ui.cards([
        (f"{res.ultimo_valor:.3f}", f"{idx.label} actual", f"{res.ultima_fecha:%d/%m/%Y}"),
        (f"{res.trend.get('slope_week', 0):+.3f}", "Tendencia semanal", "",
         ui.delta_color(res.trend.get("slope_week", 0))),
        (f"{ultimo['p10']:.2f}–{ultimo['p90']:.2f}", "Rango interno p10–p90", "última imagen"),
        (f"{res.uniformidad:.0f}" if res.uniformidad is not None else "—", "Uniformidad",
         "100 = parejo" if res.uniformidad is not None else "sin cobertura suficiente"),
        (f"{len(res.series)}", "Imágenes válidas",
         f"cada {res.gaps.get('gap_medio_dias', 0):.0f} días en promedio"),
    ])

    eventos = []
    if lote.sowing_date:
        eventos.append((lote.sowing_date, "siembra", p.muted))
    for hito, etiqueta in ((res.fenologia.sos, "inicio"), (res.fenologia.pos, "pico"),
                           (res.fenologia.eos, "fin")):
        if hito:
            eventos.append((hito, etiqueta, p.muted))
    ui.chart(charts.index_timeseries(res.series, res.curve, cfg.index, dark, eventos),
             key="veg_serie")
    st.caption(idx.reading)

    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        st.markdown("##### Fenología observada")
        filas = phenology.describe(res.fenologia, lote.sowing_date)
        if filas:
            st.dataframe(pd.DataFrame(filas, columns=["Métrica", "Valor"]),
                         use_container_width=True, hide_index=True)
            st.caption("Los hitos se calculan sobre la curva suavizada con el método de umbral "
                       "dinámico: el 35 % de la amplitud propia de esta campaña.")
        else:
            st.caption("La curva no tiene amplitud suficiente para identificar una temporada.")
    with c2:
        st.markdown("##### Uniformidad a lo largo del ciclo")
        ui.chart(charts.uniformity_chart(res.series, dark), key="veg_unif")

    ui.table_view(res.series.round(4), "Ver la serie completa en tabla")

# --------------------------------------------------------------------------
with tab_mapa:
    st.markdown("##### Mapa del índice sobre imagen satelital")
    if res.modo_demo:
        st.info("El mapa de teselas requiere Earth Engine. En modo demostración se muestra "
                "sólo el ráster sintético.", icon="🧪")
        ui.chart(charts.raster_map(res.raster, cfg.index, dark), key="veg_raster_demo")
    else:
        from agrolens.sources import gee

        c1, c2, c3 = st.columns([1, 1, 1])
        modo = c1.radio("Capa", ["Índice", "Color natural", "Comparar dos fechas"],
                        horizontal=False)
        opacidad = c2.slider("Opacidad", 0.2, 1.0, 0.85, 0.05)
        fondo = c3.selectbox("Fondo", list(maps.BASEMAPS))

        try:
            from streamlit_folium import st_folium

            if modo == "Comparar dos fechas":
                fechas = list(res.series["date"])
                col_a, col_b = st.columns(2)
                f_a = col_a.selectbox("Fecha izquierda", fechas, index=0,
                                      format_func=lambda d: d.strftime("%d/%m/%Y"))
                f_b = col_b.selectbox("Fecha derecha", fechas, index=len(fechas) - 1,
                                      format_func=lambda d: d.strftime("%d/%m/%Y"))
                url_a = gee.tile_url(gee.image_for_date(lote.geometry, f_a, cfg.index),
                                     idx.vmin, idx.vmax, idx.ramp)
                url_b = gee.tile_url(gee.image_for_date(lote.geometry, f_b, cfg.index),
                                     idx.vmin, idx.vmax, idx.ramp)
                m = maps.comparison_map(lote.geometry, url_a, url_b,
                                        f_a.strftime("%d/%m"), f_b.strftime("%d/%m"), fondo)
                maps.add_colorbar(m, list(idx.ramp), idx.vmin, idx.vmax, idx.label)
                st.caption("Arrastrá la cortina para comparar. Izquierda: "
                           f"{f_a:%d/%m/%Y} · derecha: {f_b:%d/%m/%Y}")
            elif modo == "Color natural":
                url = gee.rgb_tile_url(lote.geometry, cfg.end)
                m = maps.field_map(lote.geometry, [("Color natural", url, opacidad)], basemap=fondo)
            else:
                img = gee.composite(lote.geometry, max(cfg.start, cfg.end - timedelta(days=30)),
                                    cfg.end, cfg.index)
                url = gee.tile_url(img, idx.vmin, idx.vmax, idx.ramp)
                m = maps.field_map(
                    lote.geometry, [(f"{idx.label} (compuesto de 30 días)", url, opacidad)],
                    basemap=fondo, colorbar=(list(idx.ramp), idx.vmin, idx.vmax, idx.label),
                )
            st_folium(m, width=None, height=560, returned_objects=[], key=f"veg_mapa_{modo}")
        except ImportError:
            st.error("Falta `streamlit-folium`. Instalalo con: `pip install streamlit-folium`")
        except Exception as exc:
            st.error(f"No se pudo construir el mapa: {exc}")

    if res.raster is not None:
        c1, c2 = st.columns([1.3, 1], gap="large")
        with c1:
            ui.chart(charts.raster_map(res.raster, cfg.index, dark), key="veg_raster")
        with c2:
            ui.chart(charts.distribution(res.raster["values"], cfg.index, dark,
                                         reference=res.ultimo_valor), key="veg_hist")
            vals = res.raster["values"]
            finitos = vals[np.isfinite(vals)]
            if finitos.size:
                st.caption(
                    f"El 10 % peor del lote está por debajo de {np.percentile(finitos, 10):.2f} y "
                    f"el 10 % mejor por encima de {np.percentile(finitos, 90):.2f}. "
                    "Una distribución con dos jorobas indica ambientes bien diferenciados."
                )

# --------------------------------------------------------------------------
with tab_indices:
    st.markdown("##### Varios índices sobre el mismo lote")
    ui.note("Cada índice mira una propiedad distinta del canopeo. Verlos juntos evita "
            "conclusiones apresuradas: una caída de NDVI con NDMI estable rara vez es falta de agua.")

    elegidos = st.multiselect(
        "Índices a comparar", MAP_INDEX_ORDER,
        default=[cfg.index] + [k for k in ("NDRE", "NDMI") if k != cfg.index][:2],
        format_func=lambda k: f"{INDICES[k].label} — {FAMILY_LABELS.get(INDICES[k].family, '')}",
    )
    if elegidos and st.button("Calcular", type="primary"):
        curvas: dict = {}
        barra = st.progress(0.0)
        for i, key in enumerate(elegidos):
            barra.progress((i + 0.5) / len(elegidos), text=f"Procesando {INDICES[key].label}…")
            try:
                if res.modo_demo:
                    from agrolens.sources import demo

                    serie = demo.index_series(lote.geometry, cfg.start, cfg.end, key,
                                              lote.crop, lote.sowing_date)
                else:
                    from agrolens.sources import gee

                    serie = gee.index_series(lote.geometry, cfg.start, cfg.end, key,
                                             max_cloud=cfg.cloud_pct)
                if not serie.empty:
                    curvas[key] = timeseries.build_curve(serie, "mean", cfg.smoothing_days,
                                                         cfg.start, cfg.end)
            except Exception as exc:
                st.warning(f"{INDICES[key].label}: {exc}")
        barra.empty()
        st.session_state["curvas_comparadas"] = curvas

    curvas = st.session_state.get("curvas_comparadas") or {}
    if curvas:
        ui.chart(charts.index_comparison(curvas, dark), key="veg_comp")
        st.caption("Todos los índices se muestran normalizados de 0 a 1 dentro de su propio "
                   "rango, para que quepan en un solo eje. Los valores absolutos están en la tabla.")
        for key in curvas:
            st.markdown(f"**{INDICES[key].label}** — {INDICES[key].reading}")

# --------------------------------------------------------------------------
with tab_calidad:
    st.markdown("##### Qué tan bien vigilado está el lote")
    g = res.gaps
    ui.cards([
        (f"{g.get('n_obs', 0)}", "Imágenes válidas"),
        (f"{g.get('gap_medio_dias', 0):.0f} d", "Intervalo medio"),
        (f"{g.get('gap_max_dias', 0)} d", "Hueco más largo",
         "sin imagen utilizable", p.warning if g.get("gap_max_dias", 0) > 20 else None),
        (f"{g.get('cobertura_pct', 0):.0f} %", "Cobertura del período",
         "contra la frecuencia teórica de 5 días"),
    ])
    ui.note("Sentinel-2 pasa cada 5 días. Todo lo que falte por encima de eso son nubes: "
            "los tramos interpolados de la curva se dibujan igual, pero no son observaciones.")

    d = res.series.copy()
    d["mes"] = pd.to_datetime(d["date"]).dt.to_period("M").dt.to_timestamp()
    calidad = d.groupby("mes").agg(observaciones=("date", "count"),
                                   cobertura_media=("valid_fraction", "mean")).reset_index()
    ui.chart(charts.coverage_chart(calidad, dark), key="veg_calidad")

    ui.table_view(res.series[["date", "scene_id", "cloud_scene_pct", "valid_fraction",
                              "mean", "std"]].round(3), "Detalle por imagen")
