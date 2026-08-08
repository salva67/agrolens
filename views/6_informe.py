"""Informe: PDF, Excel y paquetes geoespaciales."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from agrolens import storage
from agrolens.config import EXPORT_DIR
from agrolens.report import exports, pdf
from agrolens.ui import components as ui

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote))

ui.hero("Informe", f"{lote.display} · {cfg.start:%d/%m/%Y} — {cfg.end:%d/%m/%Y}")
ui.note("El informe se arma con el mismo análisis que estás viendo en pantalla: nunca puede "
        "decir algo distinto de lo que muestran los gráficos.")

c1, c2, c3 = st.columns(3)
con_historia = c1.toggle(
    "Incluir comparación histórica", value=False,
    help="Reconstruye las campañas anteriores del lote. Agrega varios minutos "
         "la primera vez; después queda en caché.",
)
con_graficos = c2.toggle("Incluir gráficos", value=True,
                         help="Desactivalo para un PDF liviano, sólo con tablas.")
con_raster = c3.toggle("Incluir mapas del lote", value=True)

res = ui.get_result(lote, cfg, raster=con_raster, history=con_historia, weather=True)
ui.data_source_badge(res)

# --------------------------------------------------------------------------
score, etiqueta = res.salud()
ui.cards([
    (f"{score}", "Estado general", etiqueta),
    (f"{len(res.series)}", "Imágenes válidas"),
    (f"{len(res.alertas)}", "Hallazgos", f"{len(res.alertas_criticas)} de atención prioritaria"),
    (f"{lote.area_ha:.1f} ha", "Superficie"),
])

st.markdown("#### Hallazgos que van a ir en el informe")
ui.alert_cards(res.alertas, limit=20)

st.divider()
st.markdown("#### Descargas")

col_pdf, col_xls, col_geo = st.columns(3)

with col_pdf:
    st.markdown("**Informe PDF**")
    st.caption("Hallazgos, evidencia, tablas y metodología. Pensado para imprimir.")
    if st.button("Generar PDF", type="primary", use_container_width=True):
        with st.spinner("Armando el informe…"):
            try:
                data = pdf.build(res, include_charts=con_graficos)
                nombre = (f"{exports._slug(lote.name)}_"
                          f"{datetime.now():%Y%m%d_%H%M}.pdf")
                destino = EXPORT_DIR / nombre
                destino.write_bytes(data)
                storage.register_report(lote.id, "pdf", str(destino),
                                        {"periodo": f"{cfg.start}..{cfg.end}"})
                st.session_state["pdf_listo"] = (nombre, data)
            except pdf.PDFError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"No se pudo generar el informe: {exc}")

    listo = st.session_state.get("pdf_listo")
    if listo:
        st.download_button("Descargar PDF", listo[1], file_name=listo[0],
                           mime="application/pdf", use_container_width=True)

with col_xls:
    st.markdown("**Libro de Excel**")
    st.caption("Todas las tablas del análisis, una por hoja, con la metodología incluida.")
    try:
        st.download_button("Descargar Excel", exports.excel_workbook(res),
                           file_name=f"{exports._slug(lote.name)}_analisis.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
    except Exception as exc:
        st.error(f"No se pudo armar el Excel: {exc}")

with col_geo:
    st.markdown("**Paquete geoespacial**")
    st.caption("Ambientes en GeoJSON y shapefile, ráster en GeoTIFF y tablas asociadas.")
    if res.zonas:
        try:
            st.download_button("Descargar paquete", exports.zones_package(res),
                               file_name=f"{exports._slug(lote.name)}_ambientes.zip",
                               mime="application/zip", use_container_width=True)
        except Exception as exc:
            st.error(f"No se pudo armar el paquete: {exc}")
    else:
        st.button("Descargar paquete", disabled=True, use_container_width=True,
                  help="Activá los mapas del lote para generar la zonificación.")

# --------------------------------------------------------------------------
historial = storage.list_reports(lote.id, limit=15)
if historial:
    st.divider()
    st.markdown("#### Informes generados antes")
    import pandas as pd

    h = pd.DataFrame(historial)[["created_at", "kind", "path"]]
    h["created_at"] = pd.to_datetime(h["created_at"]).dt.strftime("%d/%m/%Y %H:%M")
    h.columns = ["Generado", "Tipo", "Archivo"]
    st.dataframe(h, use_container_width=True, hide_index=True)
    st.caption(f"Los archivos quedan en `{EXPORT_DIR}`.")
