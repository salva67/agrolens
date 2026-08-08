"""Clima y agua: lluvias, balance hídrico, suma térmica y ventanas de trabajo."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agrolens.crops import get_crop
from agrolens.ui import components as ui
from agrolens.viz import charts

ui.init_state()
lote = ui.require_lote()
cfg = ui.sidebar(lote, ui.get_config(lote), show_index=False)
crop = get_crop(lote.crop)

ui.hero("Clima y agua", f"{lote.display} · {crop.label}")
res = ui.get_result(lote, cfg, raster=False, history=False, weather=True)
ui.data_source_badge(res)

if res.clima.empty:
    st.warning("No se pudo obtener la serie climática para este lote.")
    st.stop()

dark = ui.is_dark()
p = ui.palette()
rc = res.resumen_clima or {}

# --------------------------------------------------------------------------
tarjetas = [
    (f"{rc.get('lluvia_total_mm', 0):.0f} mm", "Lluvia del período",
     f"{rc.get('dias_con_lluvia', 0)} días con lluvia"),
    (f"{rc.get('et0_total_mm', 0):.0f} mm", "Demanda atmosférica (ET0)"),
    (f"{rc.get('balance_mm', 0):+.0f} mm", "Balance P − ET0", "",
     ui.delta_color(rc.get("balance_mm", 0))),
]
if res.estres:
    aw = res.estres.get("agua_util_actual_pct", 0)
    tarjetas += [
        (f"{aw:.0f} %", "Agua útil del perfil", f"{res.balance['agua_util_mm'].iloc[-1]:.0f} mm",
         p.critical if aw < 30 else p.warning if aw < 50 else None),
        (f"{res.estres.get('dias_estres', 0)}", "Días con estrés",
         f"{res.estres.get('dias_estres_severo', 0)} severos"),
        (f"{res.estres.get('satisfaccion_hidrica', 0) * 100:.0f} %", "Satisfacción hídrica",
         "ET real sobre ET del cultivo"),
    ]
tarjetas += [
    (f"{rc.get('dias_helada', 0)}", "Días con helada"),
    (f"{rc.get('dias_calor_35', 0)}", "Días de 35 °C o más"),
]
ui.cards(tarjetas)

tab_lluvia, tab_balance, tab_termica, tab_labores = st.tabs(
    ["Lluvias", "Balance hídrico", "Térmica y fenología", "Ventanas de trabajo"]
)

# --------------------------------------------------------------------------
with tab_lluvia:
    ui.chart(charts.rain_panel(res.clima, dark), key="clima_lluvia")
    if "p30_norm" in res.clima.columns and res.clima["p30_norm"].notna().any():
        ult = res.clima.dropna(subset=["p30_norm"]).iloc[-1]
        acum30 = res.clima["precip_mm"].tail(30).sum()
        rel = acum30 - float(ult["p30_norm"])
        st.markdown(
            f"En los últimos 30 días llovieron **{acum30:.0f} mm**; la normal 1991–2020 para "
            f"esta fecha es de **{ult['p30_norm']:.0f} mm** "
            f"({'+' if rel >= 0 else ''}{rel:.0f} mm respecto de lo esperable). "
            f"El rango habitual va de {ult['p30_p20']:.0f} a {ult['p30_p80']:.0f} mm."
        )
    ui.chart(charts.rain_calendar(res.clima, dark), key="clima_calendario")

    if not res.rachas_secas.empty:
        st.markdown("##### Rachas secas del período")
        tabla = res.rachas_secas.copy()
        tabla.columns = ["Inicio", "Fin", "Días", "ET0 acumulada (mm)"]
        tabla["Inicio"] = pd.to_datetime(tabla["Inicio"]).dt.strftime("%d/%m/%Y")
        tabla["Fin"] = pd.to_datetime(tabla["Fin"]).dt.strftime("%d/%m/%Y")
        st.dataframe(tabla.round(0), use_container_width=True, hide_index=True)

    ui.table_view(res.clima[["date", "precip_mm", "et0_mm", "tmax", "tmin", "source"]].round(2),
                  "Ver la serie diaria")

# --------------------------------------------------------------------------
with tab_balance:
    if res.balance.empty:
        st.warning("No se pudo calcular el balance hídrico.")
    else:
        ui.note(
            f"Reservorio de {lote.soil_awc_mm:.0f} mm de agua útil "
            f"({lote.soil_texture.lower()}). Coeficiente de cultivo derivado de "
            f"{res.balance.attrs.get('kc_source', 'la curva teórica')}."
        )
        ui.chart(charts.water_balance_panel(res.balance, dark), key="clima_balance")

        e = res.estres
        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.markdown("##### Resumen del ciclo")
            st.dataframe(pd.DataFrame([
                ("Satisfacción hídrica del ciclo", f"{e.get('satisfaccion_hidrica', 0) * 100:.0f} %"),
                ("Satisfacción en período crítico", f"{e.get('satisfaccion_critica', 0) * 100:.0f} %"),
                ("Déficit acumulado", f"{e.get('deficit_total_mm', 0):.0f} mm"),
                ("Días con estrés en período crítico", f"{e.get('dias_estres_criticos', 0)}"),
                ("Drenaje profundo", f"{e.get('drenaje_total_mm', 0):.0f} mm"),
                ("Penalidad estimada de rinde", f"{e.get('penalidad_rinde', 0) * 100:.0f} %"),
            ], columns=["Concepto", "Valor"]), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("##### Cómo leerlo")
            st.markdown(
                "- **Ks** es el freno que el cultivo aplica a su transpiración cuando falta agua. "
                "Por debajo de 0,8 ya hay pérdida de fotosíntesis.\n"
                "- El **período crítico** de "
                f"{crop.label.lower()} pesa más que el resto del ciclo: el mismo déficit "
                "duele el doble ahí.\n"
                "- El **drenaje** alto con lluvias grandes indica que el perfil se llenó y el "
                "excedente se fue: no quedó disponible."
            )
            st.caption("Modelo FAO-56 de un reservorio. No contempla napa freática ni riego.")

        ui.table_view(res.balance[[c for c in ("date", "precip_mm", "et0_mm", "kc", "etc_mm",
                                               "eta_mm", "ks", "agua_util_mm", "agua_util_pct")
                                   if c in res.balance.columns]].round(2),
                      "Ver el balance diario")

# --------------------------------------------------------------------------
with tab_termica:
    if not res.balance.empty and "gdd_acum" in res.balance.columns:
        ui.chart(charts.gdd_chart(res.balance, crop, dark), key="clima_gdd")
        gdd_actual = float(res.balance["gdd_acum"].iloc[-1])
        etapa = crop.stage_at(gdd_actual)
        avance = gdd_actual / crop.gdd_cycle * 100 if crop.gdd_cycle else 0
        st.markdown(
            f"Suma térmica acumulada: **{gdd_actual:.0f} °C día** "
            f"({avance:.0f} % del ciclo de {crop.label.lower()}). "
            + (f"Etapa estimada: **{etapa.name}**." if etapa else "")
        )
    ui.chart(charts.temperature_panel(res.clima, crop, dark), key="clima_temp")

    if not res.eventos_termicos.empty:
        st.markdown("##### Eventos térmicos detectados")
        t = res.eventos_termicos.copy()
        t["date"] = pd.to_datetime(t["date"]).dt.strftime("%d/%m/%Y")
        t["critico"] = t["critico"].map({True: "Sí", False: "No"})
        t = t[["date", "tipo", "valor", "critico"]]
        t.columns = ["Fecha", "Tipo", "°C", "¿En período crítico?"]
        st.dataframe(t, use_container_width=True, hide_index=True)
    else:
        st.success("No se detectaron heladas ni golpes de calor por encima de los umbrales "
                   f"de {crop.label.lower()}.")

# --------------------------------------------------------------------------
with tab_labores:
    st.markdown("##### Días aptos para entrar al lote")
    ui.note("Regla explícita: sin lluvia el día, menos de 10 mm en las 48 h previas y, "
            "para pulverizar, viento por debajo de 25 km/h. Es una guía, no un permiso.")
    if res.piso.empty:
        st.caption("Sin datos suficientes.")
    else:
        futuro = res.piso[res.piso["source"] == "pronóstico"]
        vista = futuro if not futuro.empty else res.piso.tail(14)
        v = vista.copy()
        v["date"] = pd.to_datetime(v["date"]).dt.strftime("%a %d/%m")
        v["apto_piso"] = v["apto_piso"].map({True: "✅", False: "—"})
        v["apto_pulverizar"] = v["apto_pulverizar"].map({True: "✅", False: "—"})
        v = v[["date", "precip_mm", "lluvia_48h_previas", "apto_piso", "apto_pulverizar"]]
        v.columns = ["Día", "Lluvia (mm)", "Lluvia 48 h previas (mm)", "Piso", "Pulverización"]
        st.dataframe(v.round(1), use_container_width=True, hide_index=True)
        if not futuro.empty:
            aptos = int(futuro["apto_pulverizar"].sum())
            st.markdown(f"Próximos {len(futuro)} días: **{aptos} ventana(s)** aptas para pulverizar.")
