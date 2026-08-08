"""Panel: el estado del lote activo en una pantalla."""

from __future__ import annotations

import streamlit as st

from agrolens import storage
from agrolens.crops import get_crop
from agrolens.indices import get_index
from agrolens.ui import components as ui
from agrolens.viz import charts

ui.init_state()
ui.hero("Panel", "Estado del lote en una pantalla")

lotes = storage.list_lotes(ui.current_user().email)
if not lotes:
    st.info(
        "Para empezar, dibujá un lote sobre el mapa satelital o importá un archivo "
        "GeoJSON, KML o shapefile.", icon="🗺️",
    )
    if st.button("Crear el primer lote", type="primary"):
        st.switch_page("views/1_lotes.py")
    st.stop()

lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote))

c_izq, c_der = st.columns([3, 1])
with c_der:
    completo = st.toggle("Incluir historia", value=False,
                         help="Compara contra campañas anteriores. Tarda bastante más.")
    if st.button("Recalcular", use_container_width=True):
        ui.clear_results()
        st.rerun()

res = ui.get_result(lote, cfg, raster=True, history=completo, weather=True)
ui.data_source_badge(res)

# --------------------------------------------------------------------------
# Tarjetas de estado
# --------------------------------------------------------------------------
p = ui.palette()
score, etiqueta = res.salud()
score_color = (p.good if score >= 80 else p.s(5) if score >= 65
               else p.warning if score >= 45 else p.critical)
idx = get_index(cfg.index)

tarjetas = [(f"{score}", "Estado general", etiqueta, score_color)]
if res.ultimo_valor is not None:
    fecha = f"{res.ultima_fecha:%d/%m/%Y}" if res.ultima_fecha else ""
    tarjetas.append((f"{res.ultimo_valor:.2f}", f"{idx.label} actual", fecha))
if res.trend:
    sl = res.trend.get("slope_week", 0)
    flecha = "▲" if sl > 0.005 else "▼" if sl < -0.005 else "▬"
    tarjetas.append((f"{flecha} {sl:+.3f}", "Tendencia semanal", "últimas 3 semanas",
                     ui.delta_color(sl)))
if res.uniformidad is not None:
    tarjetas.append((f"{res.uniformidad:.0f}", "Uniformidad", "100 = lote parejo"))
if res.estres:
    aw = res.estres.get("agua_util_actual_pct", 0)
    tarjetas.append((f"{aw:.0f} %", "Agua útil", "del perfil explorado",
                     p.critical if aw < 30 else p.warning if aw < 50 else None))
if res.resumen_clima:
    tarjetas.append((f"{res.resumen_clima.get('lluvia_total_mm', 0):.0f} mm", "Lluvia del período",
                     f"{res.resumen_clima.get('dias_con_lluvia', 0)} días con lluvia"))
if res.resumen_historia:
    pc = res.resumen_historia.get("percentil_actual", 50)
    tarjetas.append((f"P{pc:.0f}", "Percentil histórico",
                     res.resumen_historia.get("etiqueta", ""),
                     p.good if pc >= 65 else p.critical if pc < 35 else None))
if res.rendimiento.get("estimado_tha"):
    r = res.rendimiento
    tarjetas.append((f"{r['estimado_tha']:.1f}", "Rinde estimado (t/ha)",
                     f"rango {r['rango_tha'][0]:.1f}–{r['rango_tha'][1]:.1f}"))

ui.cards(tarjetas)

# --------------------------------------------------------------------------
# Hallazgos + curva
# --------------------------------------------------------------------------
izq, der = st.columns([1.35, 1], gap="large")

with izq:
    st.markdown("#### Evolución del cultivo")
    if res.series.empty:
        st.warning("No hay observaciones satelitales válidas en el período elegido. "
                   "Probá ampliar las fechas o relajar el filtro de nubes.")
    else:
        eventos = []
        if lote.sowing_date:
            eventos.append((lote.sowing_date, "siembra", p.muted))
        if res.fenologia.pos:
            eventos.append((res.fenologia.pos, "pico", p.muted))
        ui.chart(charts.index_timeseries(res.series, res.curve, cfg.index, ui.is_dark(), eventos),
                 key="panel_serie")

with der:
    st.markdown("#### Hallazgos")
    ui.alert_cards(res.alertas, limit=6)
    if len(res.alertas) > 6:
        st.caption(f"y {len(res.alertas) - 6} hallazgo(s) más en las páginas de detalle.")

# --------------------------------------------------------------------------
# Mapa + ambientes
# --------------------------------------------------------------------------
st.divider()
m_col, z_col = st.columns([1.35, 1], gap="large")

with m_col:
    st.markdown("#### Distribución dentro del lote")
    if res.raster is not None:
        ui.chart(charts.raster_map(res.raster, cfg.index, ui.is_dark()), key="panel_raster")
        st.caption(idx.reading)
    else:
        st.caption("Sin ráster disponible para este período.")

with z_col:
    st.markdown("#### Ambientes")
    if res.zonas:
        ui.chart(charts.zone_bars(res.zonas["stats"], ui.is_dark()), key="panel_zonas")
        ui.legend([(s.label, s.color) for s in res.zonas["stats"]])
        if st.button("Ver ambientes en detalle", use_container_width=True):
            st.switch_page("views/4_ambientes.py")
    else:
        st.caption("Sin zonificación calculada.")

st.divider()
ui.download_buttons(res)
st.caption(
    f"Cultivo {get_crop(lote.crop).label} · {len(res.series)} imágenes válidas · "
    f"período {cfg.start:%d/%m/%Y}–{cfg.end:%d/%m/%Y}"
)
