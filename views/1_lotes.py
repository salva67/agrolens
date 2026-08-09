"""Gestión de lotes: dibujar, importar, guardar y elegir el lote activo."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from agrolens import storage
from agrolens.crops import CROPS, SOIL_AWC_MM_PER_M, get_crop
from agrolens.geo import GeoError, area_ha, centroid_latlon, read_uploaded, to_geojson, validate
from agrolens.models import Lote
from agrolens.ui import components as ui
from agrolens.viz import maps

ui.init_state()
ui.ephemeral_warning()
ui.hero("Lotes", "Dibujá, importá y administrá los lotes del establecimiento")

me = ui.current_user().email
lotes = storage.list_lotes(me)

# --------------------------------------------------------------------------
# Selector del lote activo
# --------------------------------------------------------------------------
top = st.columns([3, 1, 1])
with top[0]:
    if lotes:
        opciones = {l.id: (l.display if l.access == storage.DUEÑO
                           else f"{l.display}  ·  compartido por {l.owner}")
                    for l in lotes}
        ids = list(opciones)
        actual = st.session_state.get("lote_id")
        idx = ids.index(actual) if actual in ids else 0
        elegido = st.selectbox("Lote activo", ids, index=idx,
                               format_func=lambda i: opciones[i])
        if elegido != st.session_state.get("lote_id"):
            st.session_state["lote_id"] = elegido
            st.session_state["config"] = None
            ui.clear_results()
            st.rerun()
    else:
        st.info("Todavía no hay lotes guardados. Dibujá uno en el mapa o importá un archivo.")
with top[1]:
    if st.button("Ver análisis", type="primary", use_container_width=True,
                 disabled=not lotes):
        st.switch_page("views/2_vegetacion.py")
lote_actual = ui.current_lote()
soy_dueño = bool(lote_actual and lote_actual.access == storage.DUEÑO)

with top[2]:
    if st.button("Borrar lote", use_container_width=True,
                 disabled=not soy_dueño,
                 help=None if soy_dueño else "Sólo el dueño puede borrar un lote."):
        st.session_state["confirm_delete"] = True

if st.session_state.get("confirm_delete") and soy_dueño:
    st.warning(f"¿Borrar definitivamente **{lote_actual.display}**?")
    c1, c2 = st.columns([1, 6])
    if c1.button("Sí, borrar", type="primary"):
        try:
            storage.delete_lote(lote_actual.id, me)
        except storage.AccessDenied as exc:
            st.error(str(exc))
        st.session_state.update(lote_id=None, confirm_delete=False)
        ui.clear_results()
        st.rerun()
    if c2.button("Cancelar"):
        st.session_state["confirm_delete"] = False
        st.rerun()

# --------------------------------------------------------------------------
# Compartir el lote activo
# --------------------------------------------------------------------------
if soy_dueño:
    with st.expander(f"Compartir «{lote_actual.name}» con otra persona"):
        ui.note("La persona entra con su cuenta y ve este lote entre los suyos. "
                "En lectura puede analizarlo y descargar informes; en edición también "
                "puede cambiar cultivo, fechas y geometría.")
        c1, c2, c3 = st.columns([3, 2, 1])
        email_dest = c1.text_input("Email de la cuenta", placeholder="persona@ejemplo.com",
                                   key="share_email")
        permiso = c2.selectbox("Permiso", [storage.LECTURA, storage.EDICION],
                               format_func=lambda p: "Sólo lectura" if p == storage.LECTURA
                               else "Lectura y edición")
        c3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if c3.button("Compartir", use_container_width=True):
            try:
                storage.share_lote(lote_actual.id, me, email_dest, permiso)
                st.success(f"Compartido con {email_dest.strip().lower()}.")
                st.rerun()
            except (ValueError, storage.AccessDenied) as exc:
                st.error(str(exc))

        compartidos = storage.list_shares(lote_actual.id, me)
        if compartidos:
            for s in compartidos:
                cols = st.columns([4, 2, 1])
                cols[0].markdown(f"**{s['email']}**")
                cols[1].caption("sólo lectura" if s["permiso"] == storage.LECTURA
                                else "lectura y edición")
                if cols[2].button("Quitar", key=f"unshare_{s['email']}",
                                  use_container_width=True):
                    storage.unshare_lote(lote_actual.id, me, s["email"])
                    st.rerun()
        else:
            st.caption("Todavía no lo compartiste con nadie.")

elif lote_actual is not None:
    st.info(
        f"Este lote es de **{lote_actual.owner}** y lo tenés compartido en "
        f"**{'sólo lectura' if lote_actual.access == storage.LECTURA else 'lectura y edición'}**.",
        icon="🤝",
    )

st.divider()

# --------------------------------------------------------------------------
# Mapa de dibujo
# --------------------------------------------------------------------------
col_map, col_form = st.columns([1.55, 1], gap="large")

with col_map:
    st.markdown("#### Mapa")
    ui.note("Dibujá el perímetro con el polígono o el rectángulo. También podés importar "
            "un archivo desde la columna de la derecha. La última geometría dibujada es la activa.")

    basemap = st.radio("Fondo", list(maps.BASEMAPS), horizontal=True, label_visibility="collapsed")

    lote_activo = lote_actual
    centro = lote_activo.centroid if lote_activo else None
    m = maps.base_map(centro, zoom=14 if lote_activo else 6, basemap=basemap, draw=True,
                      minimap=True)
    for l in lotes:
        color = "#ffd166" if (lote_activo and l.id == lote_activo.id) else "#8ec6e0"
        maps.add_field(m, l.geometry, l.name,
                       style={"color": color, "weight": 3, "fillOpacity": 0.06},
                       tooltip=f"{l.display} · {l.area_ha:.1f} ha")
    if lote_activo:
        maps.fit(m, lote_activo.geometry)
    maps.finish(m)

    try:
        from streamlit_folium import st_folium
    except ImportError:
        st.error("Falta `streamlit-folium`. Instalalo con: `pip install streamlit-folium`")
        st.stop()

    salida = st_folium(m, width=None, height=560, returned_objects=["all_drawings"],
                       key="mapa_lotes")

    dibujos = (salida or {}).get("all_drawings") or []
    if dibujos:
        try:
            geom, avisos = validate(dibujos[-1]["geometry"])
            st.session_state["draft_geometry"] = to_geojson(geom)
            for a in avisos:
                st.warning(a, icon="⚠️")
        except GeoError as exc:
            st.error(str(exc))
            st.session_state["draft_geometry"] = None

# --------------------------------------------------------------------------
# Formulario
# --------------------------------------------------------------------------
with col_form:
    st.markdown("#### Datos del lote")

    subido = st.file_uploader(
        "Importar geometría", type=["geojson", "json", "kml", "kmz", "zip", "gpkg"],
        help="GeoJSON, KML, KMZ, GeoPackage o shapefile comprimido en ZIP.",
    )
    if subido is not None:
        try:
            geom = read_uploaded(subido.name, subido.getvalue())
            geom, avisos = validate(geom)
            st.session_state["draft_geometry"] = to_geojson(geom)
            st.success(f"Geometría importada: {area_ha(geom):.1f} ha")
            for a in avisos:
                st.warning(a, icon="⚠️")
        except GeoError as exc:
            st.error(str(exc))

    draft = st.session_state.get("draft_geometry")
    editando = lote_activo if (lote_activo and not draft) else None
    geometria = draft or (editando.geometry if editando else None)

    if geometria:
        ha = area_ha(geometria)
        lat, lon = centroid_latlon(geometria)
        ui.cards([(f"{ha:.1f}", "hectáreas"), (f"{lat:.4f}", "latitud"), (f"{lon:.4f}", "longitud")])
    else:
        st.caption("Sin geometría activa: dibujá en el mapa o importá un archivo.")

    with st.form("form_lote"):
        nombre = st.text_input("Nombre del lote", value=editando.name if editando else "")
        campo = st.text_input("Establecimiento", value=editando.farm if editando else "")

        c1, c2 = st.columns(2)
        crop_keys = list(CROPS)
        crop_actual = editando.crop if editando else "soja"
        cultivo = c1.selectbox(
            "Cultivo", crop_keys,
            index=crop_keys.index(crop_actual) if crop_actual in crop_keys else 0,
            format_func=lambda k: CROPS[k].label,
        )
        variedad = c2.text_input("Variedad / híbrido", value=editando.variety if editando else "")

        c3, c4 = st.columns(2)
        siembra_def = editando.sowing_date if (editando and editando.sowing_date) else \
            date.today() - timedelta(days=60)
        siembra = c3.date_input("Fecha de siembra", siembra_def, format="DD/MM/YYYY")
        rinde_obj = c4.number_input("Rinde objetivo (t/ha)", 0.0, 30.0,
                                    float(editando.yield_target_tha) if editando else
                                    float(get_crop(cultivo).yield_ref_tha), step=0.5)

        c5, c6 = st.columns(2)
        texturas = list(SOIL_AWC_MM_PER_M)
        textura_actual = editando.soil_texture if editando else "Franco"
        textura = c5.selectbox("Textura de suelo", texturas,
                               index=texturas.index(textura_actual) if textura_actual in texturas else 2)
        prof = c6.number_input("Profundidad explorada (m)", 0.3, 2.5, 1.0, step=0.1)
        awc = SOIL_AWC_MM_PER_M[textura] * prof
        st.caption(f"Agua útil estimada del perfil: **{awc:.0f} mm**. "
                   "Si tenés análisis de suelo, ajustá la profundidad para acercarte al valor real.")

        notas = st.text_area("Notas", value=editando.notes if editando else "", height=70)

        solo_lectura = bool(editando and editando.access == storage.LECTURA)
        guardar = st.form_submit_button(
            "Guardar lote", type="primary", use_container_width=True, disabled=solo_lectura,
        )
    if solo_lectura:
        st.caption(f"Compartido en sólo lectura por {editando.owner}: no podés modificarlo.")

    if guardar:
        if not geometria:
            st.error("Falta la geometría: dibujá el lote en el mapa o importá un archivo.")
        elif not nombre.strip():
            st.error("Poné un nombre al lote para poder encontrarlo después.")
        else:
            lote = editando or Lote(name=nombre.strip(), geometry=geometria)
            lote.name = nombre.strip()
            lote.farm = campo.strip()
            lote.geometry = geometria
            lote.crop = cultivo
            lote.variety = variedad.strip()
            lote.sowing_date = siembra
            lote.soil_texture = textura
            lote.soil_awc_mm = float(awc)
            lote.yield_target_tha = float(rinde_obj)
            lote.notes = notas
            try:
                storage.save_lote(lote, me)
            except storage.AccessDenied as exc:
                st.error(str(exc))
            else:
                st.session_state.update(lote_id=lote.id, draft_geometry=None)
                ui.clear_results()
                st.success(f"Lote **{lote.name}** guardado ({lote.area_ha:.1f} ha).")
                st.rerun()

# --------------------------------------------------------------------------
# Listado
# --------------------------------------------------------------------------
if lotes:
    st.divider()
    st.markdown("#### Lotes guardados")
    import pandas as pd

    tabla = pd.DataFrame([{
        "Lote": l.name, "Establecimiento": l.farm, "Cultivo": get_crop(l.crop).label,
        "Superficie (ha)": round(l.area_ha, 1),
        "Siembra": l.sowing_date.strftime("%d/%m/%Y") if l.sowing_date else "—",
        "Suelo": l.soil_texture, "Agua útil (mm)": round(l.soil_awc_mm),
        "Acceso": "Propio" if l.access == storage.DUEÑO
                  else f"Compartido por {l.owner} ({l.access})",
    } for l in lotes])
    st.dataframe(tabla, use_container_width=True, hide_index=True)
    propios = [l for l in lotes if l.access == storage.DUEÑO]
    st.caption(
        f"{len(propios)} lote(s) propio(s) · {sum(l.area_ha for l in propios):,.0f} ha"
        + (f" · {len(lotes) - len(propios)} compartido(s) con vos" if len(lotes) > len(propios) else "")
    )

ui.sidebar(lote_actual)
