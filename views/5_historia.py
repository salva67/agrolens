"""Historia: la campaña actual contra las anteriores del mismo lote."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agrolens.analytics import anomaly
from agrolens.indices import get_index
from agrolens.ui import components as ui
from agrolens.viz import charts

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote))
idx = get_index(cfg.index)

ui.hero("Historia", f"{lote.display} · últimas {cfg.history_years} campañas")

if not lote.sowing_date:
    st.warning("La comparación histórica necesita la fecha de siembra para alinear las campañas. "
               "Cargala en la página **Lotes**.")
    st.stop()

ui.note(
    "Cada campaña se alinea por días desde la siembra, no por fecha calendario: así se compara "
    "el cultivo en la misma etapa, aunque un año se haya sembrado tres semanas más tarde."
)

res = ui.get_result(lote, cfg, raster=True, history=True, weather=True)
ui.data_source_badge(res)

if res.historia.empty:
    st.warning("No se pudieron reconstruir campañas anteriores. Puede que el lote no tenga "
               "suficientes imágenes limpias en años previos.")
    st.stop()

dark = ui.is_dark()
p = ui.palette()
resumen = res.resumen_historia or {}

color_pct = (p.good if resumen.get("percentil_actual", 50) >= 65
             else p.critical if resumen.get("percentil_actual", 50) < 35 else None)
ui.cards([
    (f"P{resumen.get('percentil_actual', 0):.0f}", "Percentil histórico",
     resumen.get("etiqueta", ""), color_pct),
    (f"{resumen.get('z_actual', 0):+.2f}", "Desvíos estándar", "respecto de la media histórica"),
    (f"{resumen.get('anomalia_actual', 0):+.3f}", f"Anomalía de {idx.label}",
     "contra la mediana de las campañas previas"),
    (f"{resumen.get('campañas_comparadas', 0)}", "Campañas comparadas"),
    (f"{resumen.get('dias_bajo_p25', 0)}", "Días bajo el percentil 25",
     "en lo que va del ciclo"),
])

tab_banda, tab_campanas, tab_anomalia = st.tabs(
    ["Banda histórica", "Campaña por campaña", "Anomalía diaria"]
)

with tab_banda:
    ui.chart(charts.history_envelope(res.banda, res.ranking, cfg.index, dark), key="hist_banda")
    st.markdown(
        "La banda gris muestra dónde estuvo este lote en campañas anteriores a la misma altura "
        "del ciclo. Si la línea azul sale por debajo del rango p10–p90, es un año fuera de lo "
        "normal para **este** lote, no para la zona."
    )
    ui.table_view(res.banda.round(3), "Ver la banda histórica en tabla")

with tab_campanas:
    actual = res.ranking[["das", "valor"]] if not res.ranking.empty else None
    ui.chart(charts.season_comparison(res.historia, actual, dark, index_key=cfg.index),
             key="hist_campanas")

    resumen_camp = (res.historia.groupby("campaña")["smooth"]
                    .agg(["max", "mean"]).round(3).reset_index())
    resumen_camp.columns = ["Campaña", f"{idx.label} máximo", f"{idx.label} medio"]
    integral = (res.historia.groupby("campaña")["smooth"]
                .apply(lambda s: float((s - 0.15).clip(lower=0).sum())).round(0))
    resumen_camp["Integral (índice·día)"] = resumen_camp["Campaña"].map(integral)
    st.dataframe(resumen_camp, use_container_width=True, hide_index=True)
    st.caption("La integral acumula el índice por encima de 0,15 a lo largo del ciclo: es el "
               "mejor proxy satelital de biomasa total producida.")

with tab_anomalia:
    if res.ranking.empty:
        st.caption("Sin superposición suficiente entre la campaña actual y la historia.")
    else:
        ui.chart(charts.anomaly_bars(res.ranking, dark), key="hist_anomalia")
        r = res.ranking.copy()
        r["Estado"] = r["percentil"].map(lambda x: anomaly.classify(x)[0])
        vista = r[["date", "das", "valor", "percentil", "z", "anomalia", "Estado"]].round(3)
        vista.columns = ["Fecha", "Días desde siembra", idx.label, "Percentil", "Z",
                         "Anomalía", "Estado"]
        vista["Fecha"] = pd.to_datetime(vista["Fecha"]).dt.strftime("%d/%m/%Y")
        ui.table_view(vista, "Ver el detalle diario")
