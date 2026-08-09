"""Tormentas: exposición a granizo y viento, y daño observado desde el satélite."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agrolens.analytics import storms as smod
from agrolens.crops import get_crop
from agrolens.sources import hail
from agrolens.ui import components as ui
from agrolens.viz import charts

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote), show_index=False)
crop = get_crop(lote.crop)

ui.hero("Tormentas", f"{lote.display} · granizo y viento")
res = ui.get_result(lote, cfg, raster=False, history=False, weather=True)
ui.data_source_badge(res)

if res.clima.empty:
    st.warning("Sin datos climáticos no se puede evaluar la exposición a tormentas.")
    st.stop()

dark = ui.is_dark()
p = ui.palette()
resumen = res.resumen_tormentas or {}

# --------------------------------------------------------------------------
rafaga = resumen.get("rafaga_max_kmh")
ui.cards([
    (f"{resumen.get('eventos', 0)}", "Días con tormenta", "en el período analizado"),
    (f"{resumen.get('con_granizo', 0)}", "Con granizo declarado", "según el modelo meteorológico",
     p.critical if resumen.get("con_granizo") else None),
    (f"{rafaga:.0f} km/h" if rafaga else "—", "Ráfaga máxima",
     f"{resumen.get('dias_rafaga_dano', 0)} día(s) sobre 80 km/h",
     p.critical if (rafaga or 0) >= 100 else p.warning if (rafaga or 0) >= 80 else None),
    (f"{len(res.dano_tormenta)}", "Daños sospechados", "caídas del índice junto a tormenta",
     p.critical if len(res.dano_tormenta) else None),
    (f"{resumen.get('eventos_pronosticados', 0)}", "Pronosticados", "próximos días"),
])

tab_dano, tab_eventos, tab_goes = st.tabs(
    ["Daño detectado", "Eventos del período", "Análisis GOES de granizo"]
)

# --------------------------------------------------------------------------
with tab_dano:
    st.markdown("##### Caídas del índice que coinciden con una tormenta")
    ui.note(
        "El dato climático dice que **pudo** pasar; el satélite dice que **pasó**. Se buscan "
        "caídas bruscas del índice entre dos imágenes cercanas con una tormenta en el medio. "
        "El granizo y el viento destruyen tejido verde de un día para el otro; la seca y las "
        "enfermedades bajan el índice de a poco."
    )

    if res.dano_tormenta.empty:
        st.success(
            "No se detectaron caídas del índice atribuibles a tormentas en el período. "
            "Esto no descarta daño leve: una pérdida de hojas que el cultivo recompone en "
            "una semana puede no dejar rastro entre dos pasadas del satélite."
        )
    else:
        for _, d in res.dano_tormenta.iterrows():
            color = {"alta": p.critical, "media": p.serious}.get(d["confianza"], p.warning)
            que = "granizo" if d["granizo"] else "tormenta"
            st.markdown(
                f'<div class="al-alert" style="border-left-color:{color}">'
                f'<div class="t">Posible daño por {que} · '
                f'{pd.Timestamp(d["tormenta"]):%d/%m/%Y}'
                f'<span class="tag">confianza {d["confianza"]}</span></div>'
                f'<div class="m">El índice cayó <b>{d["caida"]:.2f}</b> '
                f'({d["valor_antes"]:.2f} → {d["valor_despues"]:.2f}) entre el '
                f'{pd.Timestamp(d["fecha_antes"]):%d/%m} y el '
                f'{pd.Timestamp(d["fecha_despues"]):%d/%m}, {d["dias"]} días. '
                f'Registrado ese día: {d["detalle"]}.</div>'
                f'<div class="r"><b>Qué hacer:</b> recorrer y documentar con fotos fechadas. '
                f'Si hay seguro, avisar ahora.</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("##### Cómo leer la confianza")
        st.markdown(
            "- **Alta** — caída grande, imágenes cercanas y tormenta severa. La atribución es sólida.\n"
            "- **Media** — falta alguno de los tres. Probable, pero conviene verificar a campo.\n"
            "- **Baja** — hay coincidencia temporal y poco más. Entre las dos imágenes pudo pasar "
            "cualquier otra cosa."
        )
        ui.table_view(res.dano_tormenta, "Ver el detalle en tabla")

    if not res.series.empty:
        eventos = [(pd.Timestamp(d["tormenta"]).date(),
                    "granizo" if d["granizo"] else "tormenta", p.critical)
                   for _, d in res.dano_tormenta.iterrows()]
        ui.chart(charts.index_timeseries(res.series, res.curve, cfg.index, dark, eventos),
                 key="storm_serie")

# --------------------------------------------------------------------------
with tab_eventos:
    if res.tormentas.empty:
        st.success("No se registraron tormentas en el período analizado.")
    else:
        ui.chart(charts.storm_timeline(res.tormentas, res.clima, dark), key="storm_timeline")

        t = res.tormentas.copy()
        t["Fecha"] = pd.to_datetime(t["date"]).dt.strftime("%d/%m/%Y")
        cols = {"Fecha": "Fecha", "tipo": "Qué se registró", "severidad": "Severidad",
                "rafaga_kmh": "Ráfaga (km/h)", "lluvia_mm": "Lluvia (mm)",
                "source": "Origen"}
        if "en_periodo_critico" in t.columns:
            t["en_periodo_critico"] = t["en_periodo_critico"].map({True: "Sí", False: "—"})
            cols["en_periodo_critico"] = "¿Período crítico?"
        vista = t[[c for c in cols if c in t.columns]].rename(columns=cols)
        st.dataframe(vista, use_container_width=True, hide_index=True)
        st.caption(
            f"El período crítico de {crop.label.lower()} es donde el mismo evento cuesta más: "
            "antes de floración el cultivo suele recomponer, durante el llenado no."
        )

# --------------------------------------------------------------------------
with tab_goes:
    st.markdown("##### Exposición a granizo medida sobre la tormenta")
    ui.note(
        "El registro meteorológico dice si hubo granizo *en la zona*. Esto mide la estructura "
        "vertical de la tormenta sobre un disco alrededor del lote, con imágenes GOES cada "
        "10 minutos: topes fríos, overshooting y duración. Es lo que sirve para dimensionar "
        "un evento puntual."
    )

    if not hail.available():
        st.info(
            "El análisis GOES necesita el paquete `granizo_riesgo`, que no está disponible en "
            "este entorno. La exposición por ráfagas y códigos meteorológicos de las otras "
            "pestañas sigue funcionando sin él.",
            icon="🛰️",
        )
        st.caption("Para habilitarlo en el servidor, copiá la carpeta `granizo_riesgo/` "
                   "dentro del repositorio de AgroLens.")
    else:
        candidatas = res.tormentas[res.tormentas["source"] != "pronóstico"] \
            if "source" in res.tormentas else res.tormentas
        if candidatas.empty:
            st.caption("No hay días con tormenta para analizar en este período.")
        else:
            opciones = list(candidatas.sort_values("severidad", ascending=False)["date"])
            c1, c2 = st.columns([2, 1])
            fechas = c1.multiselect(
                "Días a analizar", opciones, default=opciones[:3],
                format_func=lambda d: (f"{pd.Timestamp(d):%d/%m/%Y} — "
                                       f"{candidatas.loc[candidatas['date'] == d, 'tipo'].iloc[0]}"),
                help="Cada fecha es una consulta a Earth Engine. Empezá por las más severas.",
            )
            radio = c2.slider("Radio del disco (km)", 5, 40, 20, 5,
                              help="ABI mide ~4 km sobre Argentina: un lote es subpíxel, "
                                   "por eso se evalúa un disco alrededor.")

            if fechas and st.button("Analizar con GOES", type="primary"):
                lat, lon = lote.centroid
                barra = st.progress(0.0)
                try:
                    resultados = hail.evaluate_days(lat, lon, fechas, radio,
                                                    progress=lambda p_, m: barra.progress(p_, text=m))
                    st.session_state["granizo_goes"] = resultados
                except hail.HailEngineUnavailable as exc:
                    st.error(str(exc))
                finally:
                    barra.empty()

            goes = st.session_state.get("granizo_goes") or []
            if goes:
                ui.cards([
                    (f"{g['score']:.0f}" if g.get("score") is not None else "—",
                     f"{pd.Timestamp(g['fecha']):%d/%m}", g.get("categoria", ""),
                     hail.category_color(g.get("categoria", "")))
                    for g in goes
                ])
                tabla = pd.DataFrame([{
                    "Fecha": f"{pd.Timestamp(g['fecha']):%d/%m/%Y}",
                    "Puntaje": None if g.get("score") is None else round(g["score"], 1),
                    "Categoría": g.get("categoria"),
                    "Tope más frío (K)": (round(g["bt_min_k"], 1)
                                          if g.get("bt_min_k") is not None else None),
                    "Pico": (pd.Timestamp(g["pico"]["t_utc"]).strftime("%H:%M UTC")
                             if isinstance(g.get("pico"), dict) and g["pico"].get("t_utc") else "—"),
                } for g in goes])
                st.dataframe(tabla, use_container_width=True, hide_index=True)
                st.caption(
                    "Categorías: menos de 15 muy bajo · 15–30 bajo · 30–50 moderado · "
                    "50–70 alto · 70 o más muy alto. Es **exposición, no probabilidad de "
                    "granizo**: el satélite ve topes de nube, no piedras. Ordena bien el "
                    "riesgo relativo entre lotes y fechas; no resuelve un siniestro por sí solo."
                )
