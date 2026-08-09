"""Ambientes: zonificación intra-lote, estabilidad entre campañas y prescripción."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import streamlit as st

from agrolens.analytics import zones as zmod
from agrolens.indices import get_index
from agrolens.report import exports
from agrolens.ui import components as ui
from agrolens.viz import charts, maps

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote))
idx = get_index(cfg.index)

ui.hero("Ambientes", f"{lote.display} · {lote.area_ha:.1f} ha")
res = ui.get_result(lote, cfg, raster=True, history=False, weather=False)
ui.data_source_badge(res)

if not res.zonas:
    st.warning("No se pudo generar la zonificación. Revisá el período o la disponibilidad "
               "de imágenes sin nubes.")
    st.stop()

dark = ui.is_dark()
zonas = res.zonas
stats = zonas["stats"]

tab_zonas, tab_estabilidad, tab_presc = st.tabs(
    ["Zonas de la campaña", "Estabilidad entre campañas", "Prescripción"]
)

# --------------------------------------------------------------------------
with tab_zonas:
    ui.note(
        f"Agrupación de los píxeles del lote según el {idx.label} del compuesto de los últimos "
        "30 días. La zona 1 es siempre la de menor vigor: el color significa lo mismo en "
        "todas las corridas."
    )
    ui.cards([
        (f"{len(stats)}", "Ambientes"),
        (f"{stats[0].area_ha:.1f} ha", "Ambiente de menor vigor", f"{stats[0].pct:.0f} % del lote"),
        (f"{stats[-1].mean - stats[0].mean:.2f}", "Brecha entre extremos", idx.label),
        (f"{zonas.get('separacion', 0):.1f}", "Separación entre zonas",
         "mayor a 1 = ambientes bien diferenciados"),
    ])

    c1, c2 = st.columns([1.3, 1], gap="large")
    with c1:
        ui.chart(charts.zone_map(zonas, dark), key="amb_mapa")
        ui.legend([(f"{s.label} · {s.area_ha:.1f} ha", s.color) for s in stats])
    with c2:
        ui.chart(charts.zone_bars(stats, dark, value_label=f"{idx.label} medio"), key="amb_barras")
        ui.chart(charts.distribution(res.raster["values"], cfg.index, dark, height=260),
                 key="amb_dist")

    st.markdown("##### Detalle por ambiente")
    tabla = pd.DataFrame([{
        "Ambiente": s.label, "Superficie (ha)": s.area_ha, "% del lote": s.pct,
        f"{idx.label} medio": round(s.mean, 3), "Desvío": round(s.std, 3),
    } for s in stats])
    st.dataframe(tabla, use_container_width=True, hide_index=True)

    st.markdown("##### Ver los ambientes sobre el mapa satelital")
    try:
        from streamlit_folium import st_folium

        gdf = zmod.zone_polygons(zonas)
        m = maps.field_map(lote.geometry, zones_gdf=gdf,
                           legend=[(s.label, s.color) for s in stats])
        st_folium(m, width=None, height=520, returned_objects=[], key="amb_folium")
    except ImportError:
        st.error("Falta `streamlit-folium`. Instalalo con: `pip install streamlit-folium`")
    except Exception as exc:
        st.warning(f"No se pudieron vectorizar las zonas: {exc}")

# --------------------------------------------------------------------------
with tab_estabilidad:
    ui.note(
        "Cruza el promedio de varias campañas con su variabilidad. Lo estable es estructural "
        "(suelo, drenaje, topografía) y justifica inversión; lo inestable depende del año y se "
        "maneja con decisiones tácticas."
    )
    n_campanas = st.slider("Campañas a cruzar", 2, 6, 3)
    if st.button("Calcular estabilidad", type="primary"):
        if res.modo_demo:
            st.info("En modo demostración la estabilidad no es representativa.", icon="🧪")
        with st.spinner("Descargando compuestos de cada campaña…"):
            rasters = []
            errores = []
            barra = st.progress(0.0)
            for k in range(n_campanas):
                barra.progress((k + 0.5) / n_campanas, text=f"Campaña {cfg.end.year - k}")
                try:
                    if res.modo_demo:
                        from agrolens.sources import demo

                        rasters.append(demo.raster(lote.geometry, cfg.index,
                                                   0.6 + 0.05 * ((-1) ** k)))
                    else:
                        from agrolens.sources import gee

                        fin = cfg.end.replace(year=cfg.end.year - k)
                        rasters.append(gee.download_index_raster(
                            lote.geometry, fin - timedelta(days=45), fin, cfg.index,
                            mode="composite"))
                except Exception as exc:
                    errores.append(f"{cfg.end.year - k}: {exc}")
            barra.empty()
            for e in errores:
                st.warning(e)
            try:
                st.session_state["estabilidad"] = zmod.stability_zones(rasters)
            except Exception as exc:
                st.error(f"No se pudo calcular la estabilidad: {exc}")

    est = st.session_state.get("estabilidad")
    if est:
        c1, c2 = st.columns([1.3, 1], gap="large")
        with c1:
            ui.chart(charts.zone_map(est, dark, title=f"Estabilidad ({est['campañas']} campañas)"),
                     key="est_mapa")
            ui.legend([(s.label, s.color) for s in est["stats"]])
        with c2:
            ui.chart(charts.zone_bars(est["stats"], dark, value_label="Índice normalizado medio"),
                     key="est_barras")
        st.markdown("##### Qué hacer con cada clase")
        for s in est["stats"]:
            if s.area_ha <= 0:
                continue
            desc = est["descripciones"].get(s.label, "")
            st.markdown(f'<span style="color:{s.color}">●</span> **{s.label}** '
                        f'({s.area_ha:.1f} ha · {s.pct:.0f} %) — {desc}',
                        unsafe_allow_html=True)

# --------------------------------------------------------------------------
with tab_presc:
    ui.note("Traduce los ambientes a dosis, manteniendo el promedio del lote para no alterar "
            "el presupuesto de insumo. Verificá siempre contra análisis de suelo.")

    c1, c2, c3 = st.columns(3)
    insumo = c1.selectbox("Insumo", ["Nitrógeno (kg/ha)", "Fósforo (kg/ha)",
                                     "Densidad de siembra (semillas/ha)", "Otro"])
    dosis_base = c2.number_input("Dosis media del lote", 0.0, 500_000.0, 120.0, step=10.0)
    amplitud = c3.slider("Amplitud entre ambientes (%)", 0, 60, 25, 5)

    estrategia = st.radio(
        "Criterio",
        ["compensar", "potenciar", "uniforme"],
        horizontal=True,
        format_func=lambda k: {
            "compensar": "Compensar — más insumo donde el cultivo está peor",
            "potenciar": "Potenciar — más insumo donde el cultivo responde mejor",
            "uniforme": "Uniforme — testigo para comparar",
        }[k],
    )
    st.caption({
        "compensar": "Habitual en nitrógeno cuando la limitante es corregible y los ambientes "
                     "tienen potencial similar.",
        "potenciar": "Habitual en fósforo o densidad, cuando el ambiente pobre no va a responder.",
        "uniforme": "La práctica actual, para medir contra ella.",
    }[estrategia])

    unidad = "semillas_ha" if "semillas" in insumo else "kg_ha"
    presc = zmod.prescription(stats, dosis_base, estrategia, float(amplitud), unidad.replace("_", "/"))

    if presc.empty:
        st.warning("No hay ambientes válidos para prescribir.")
    else:
        c1, c2 = st.columns([1.2, 1], gap="large")
        with c1:
            ui.chart(charts.prescription_chart(presc, dark), key="presc_chart")
        with c2:
            vista = presc.drop(columns=["color"])
            st.dataframe(vista, use_container_width=True, hide_index=True)
            total = float(presc["insumo_total"].sum())
            medio = total / max(1e-9, float(presc["superficie_ha"].sum()))
            st.markdown(f"Insumo total: **{total:,.0f}** · dosis media ponderada: "
                        f"**{medio:,.1f}** (objetivo {dosis_base:,.1f}).")

        st.markdown("##### Exportar")
        c1, c2 = st.columns(2)
        with c1:
            try:
                paquete = exports.zones_package(res, presc)
                st.download_button("Paquete completo (GeoJSON + SHP + CSV + GeoTIFF)", paquete,
                                   file_name=f"{exports._slug(lote.name)}_prescripcion.zip",
                                   mime="application/zip", use_container_width=True,
                                   type="primary")
            except Exception as exc:
                st.error(f"No se pudo armar el paquete: {exc}")
        with c2:
            st.download_button("Tabla de prescripción (CSV)", exports.csv_bytes(presc),
                               file_name=f"{exports._slug(lote.name)}_prescripcion.csv",
                               mime="text/csv", use_container_width=True)
