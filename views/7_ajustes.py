"""Ajustes: estado de las conexiones, caché y diagnóstico."""

from __future__ import annotations

import platform
import sys

import streamlit as st

from agrolens import cache, storage
from agrolens.config import (
    APP_NAME, APP_VERSION, CACHE_DIR, DATA_DIR, DB_PATH, SETTINGS, in_synced_folder,
)
from agrolens.ui import components as ui

ui.init_state()
ui.hero("Ajustes", "Conexiones, caché y diagnóstico")

# --------------------------------------------------------------------------
st.markdown("#### Fuentes de datos")

c1, c2 = st.columns(2, gap="large")

with c1:
    st.markdown("**Google Earth Engine** — imágenes Sentinel-2")
    if st.button("Probar conexión", use_container_width=True):
        with st.spinner("Conectando…"):
            try:
                from agrolens.sources import gee

                gee.init(force=True)
                estado = gee.status()
                st.success(f"Conectado al proyecto `{SETTINGS.ee_project}`.")
                # Saber CON QUÉ credencial conectó es lo que distingue una
                # instalación lista para el servidor de una que sólo anda acá.
                credencial = estado.get("credencial") or "desconocida"
                if "servicio" in credencial:
                    st.markdown(f"Credencial: **{credencial}** — lista para publicar.")
                else:
                    st.markdown(f"Credencial: **{credencial}**")
                    st.caption(
                        "Es una credencial personal: sirve en esta máquina, pero no en un "
                        "servidor. Para publicar, cargá una cuenta de servicio en "
                        "`EE_SERVICE_ACCOUNT_JSON`."
                    )
            except Exception as exc:
                st.error(str(exc))
                st.markdown(
                    "**Cómo resolverlo**\n\n"
                    "1. Instalá el cliente: `pip install earthengine-api`\n"
                    "2. Autenticá una vez: `earthengine authenticate`\n"
                    "3. Definí el proyecto: variable de entorno `EE_PROJECT`, o "
                    "`.streamlit/secrets.toml` con `EE_PROJECT = \"tu-proyecto\"`\n\n"
                    "En un servidor sin navegador, usá una cuenta de servicio y cargá el JSON "
                    "en `EE_SERVICE_ACCOUNT_JSON`."
                )
    st.caption(f"Proyecto configurado: `{SETTINGS.ee_project or 'no definido'}`")

with c2:
    st.markdown("**Open-Meteo** — clima observado y pronóstico")
    if st.button("Probar clima", use_container_width=True):
        with st.spinner("Consultando…"):
            try:
                from datetime import date, timedelta

                from agrolens.sources import weather

                df = weather.archive(-36.75, -62.95, date.today() - timedelta(days=10),
                                     date.today() - timedelta(days=6), refresh=True)
                st.success(f"Respondió correctamente: {len(df)} días recibidos.")
            except Exception as exc:
                st.error(str(exc))
    st.caption("No requiere clave de API ni cuenta.")

st.divider()

# --------------------------------------------------------------------------
st.markdown("#### Caché")
info = cache.stats()
ui.cards([
    (f"{info['archivos']}", "Objetos en caché"),
    (f"{info['tamaño_mb']:.1f} MB", "Espacio usado"),
    (f"{SETTINGS.cache_ttl_hours} h", "Vigencia por defecto"),
])
st.caption(f"Los resultados se guardan en `{CACHE_DIR}` y sobreviven entre sesiones: "
           "reabrir un lote ya consultado es instantáneo.")

c1, c2, c3 = st.columns(3)
if c1.button("Vaciar caché satelital", use_container_width=True):
    n = cache.clear("s2series") + cache.clear("s2raster") + cache.clear("s2rgb")
    st.success(f"{n} objeto(s) eliminados.")
if c2.button("Vaciar caché de clima", use_container_width=True):
    n = (cache.clear("wx-archive") + cache.clear("wx-forecast")
         + cache.clear("wx-bundle") + cache.clear("wx-climatology"))
    st.success(f"{n} objeto(s) eliminados.")
if c3.button("Vaciar todo el caché", use_container_width=True):
    st.success(f"{cache.clear()} objeto(s) eliminados.")
    ui.clear_results()

st.divider()

# --------------------------------------------------------------------------
st.markdown("#### Tu cuenta y tus datos")

from agrolens.ui import auth  # noqa: E402

usuario = ui.current_user()
resumen = storage.stats(usuario.email)
lotes = storage.list_lotes(usuario.email)

modo_txt = {"oidc": "Cuenta de Google", "clave": "Clave compartida",
            "local": "Sesión local sin autenticación"}[auth.mode()]
ui.cards([
    (usuario.display, "Sesión iniciada", usuario.email if usuario.modo == "oidc" else modo_txt),
    (f"{resumen['propios']}", "Lotes propios", f"{resumen['hectareas']:,.0f} ha"),
    (f"{resumen['compartidos_conmigo']}", "Compartidos conmigo"),
    (f"{resumen['compartidos_por_mi']}", "Compartidos por mí"),
    (f"{len(storage.list_reports(limit=999))}", "Informes generados"),
])

if auth.mode() == "local":
    st.info(
        "La app corre sin autenticación: todo lo que se guarde queda bajo la identidad "
        "`local`. Para compartirla con otras personas configurá el login en "
        "`secrets.toml`; está explicado en el README.", icon="🔓",
    )
elif auth.mode() == "oidc":
    emails, dominios = auth._allowlist()
    if emails or dominios:
        st.caption("Acceso restringido a: " + ", ".join(sorted(emails | {"@" + d for d in dominios})))
    else:
        st.warning(
            "No hay lista blanca configurada: cualquier persona con una cuenta de Google "
            "puede entrar y crear sus propios lotes. Definí `AGROLENS_ALLOWED_EMAILS` o "
            "`AGROLENS_ALLOWED_DOMAIN` para limitarlo.", icon="⚠️",
        )

    pendientes = storage.count_local_lotes()
    if pendientes:
        st.info(
            f"Hay **{pendientes} lote(s)** cargados antes de activar el login, bajo la "
            "identidad `local`. Nadie los ve hasta que alguien los adopte.", icon="📥",
        )
        if st.button(f"Pasar esos {pendientes} lote(s) a mi cuenta", type="primary"):
            n = storage.claim_local(usuario.email)
            st.success(f"{n} lote(s) ahora son tuyos.")
            st.rerun()

st.caption(f"Base local: `{DB_PATH}`")

sincronizada = in_synced_folder()
if sincronizada:
    st.error(
        f"**Los datos están dentro de {sincronizada}.** SQLite usa bloqueos de archivo y el "
        f"cliente de sincronización puede corromper la base mientras se escribe: se perderían "
        f"todos los lotes. Movelos a una carpeta local y definí `AGROLENS_DATA_DIR` "
        f"apuntando ahí.", icon="⚠️",
    )

st.caption(
    "Copia de seguridad: en un servidor con disco efímero (por ejemplo Streamlit "
    "Community Cloud) los lotes se pierden cuando la app se reinicia. Exportalos y "
    "volvelos a importar cuando haga falta."
)

col_exp, col_imp = st.columns(2, gap="large")

with col_exp:
    from agrolens.geo import to_feature_collection, to_geojson_feature
    from agrolens.report.exports import geojson_bytes

    propios = [l for l in lotes if l.access == storage.DUEÑO]
    if propios:
        fc = to_feature_collection([
            to_geojson_feature(l.geometry, {k: v for k, v in l.to_dict().items()
                                            if k not in ("geometry", "access")})
            for l in propios
        ])
        st.download_button("Exportar todos los lotes (GeoJSON)", geojson_bytes(fc),
                           file_name="agrolens_lotes.geojson", mime="application/geo+json",
                           use_container_width=True)
    else:
        st.button("Exportar todos los lotes (GeoJSON)", disabled=True,
                  use_container_width=True, help="Todavía no hay lotes guardados.")

with col_imp:
    subido = st.file_uploader("Restaurar lotes desde una copia", type=["geojson", "json"],
                              key="restore_lotes")
    if subido is not None and st.button("Importar", type="primary", use_container_width=True):
        import json

        from agrolens.geo import area_ha, centroid_latlon
        from agrolens.models import Lote

        try:
            data = json.loads(subido.getvalue().decode("utf-8"))
            feats = data.get("features", []) if data.get("type") == "FeatureCollection" else []
            if not feats:
                st.error("El archivo no tiene features: ¿es una copia exportada por AgroLens?")
            else:
                existentes = {l.id for l in lotes}
                nuevos = actualizados = rechazados = 0
                for f in feats:
                    props = dict(f.get("properties") or {})
                    props["geometry"] = f["geometry"]
                    props.setdefault("name", "Lote importado")
                    props.pop("owner", None)  # el dueño lo define quien importa
                    lote = Lote.from_dict(props)
                    lote.area_ha = area_ha(lote.geometry)
                    lote.centroid = centroid_latlon(lote.geometry)
                    try:
                        storage.save_lote(lote, usuario.email)
                    except storage.AccessDenied:
                        rechazados += 1  # id ya usado por otra cuenta
                        continue
                    if lote.id in existentes:
                        actualizados += 1
                    else:
                        nuevos += 1
                msg = f"{nuevos} lote(s) nuevo(s) y {actualizados} actualizado(s)."
                if rechazados:
                    msg += f" {rechazados} pertenecen a otra cuenta y se omitieron."
                st.success(msg)
                st.rerun()
        except Exception as exc:
            st.error(f"No se pudo importar: {exc}")

st.divider()

# --------------------------------------------------------------------------
with st.expander("Diagnóstico del entorno"):
    filas = [
        ("Aplicación", f"{APP_NAME} v{APP_VERSION}"),
        ("Python", sys.version.split()[0]),
        ("Sistema", f"{platform.system()} {platform.release()}"),
        ("Directorio de datos", str(DATA_DIR)),
        ("Modo demostración", "activo" if st.session_state.get("demo_mode") else "inactivo"),
    ]
    for mod in ("streamlit", "streamlit_folium", "folium", "plotly", "geopandas", "rasterio",
                "sklearn", "ee", "reportlab", "kaleido"):
        try:
            import importlib

            m = importlib.import_module(mod)
            filas.append((mod, getattr(m, "__version__", "instalado")))
        except Exception:
            filas.append((mod, "NO INSTALADO"))

    import pandas as pd

    st.dataframe(pd.DataFrame(filas, columns=["Componente", "Estado"]),
                 use_container_width=True, hide_index=True)
    st.caption("Si falta `streamlit_folium`, `reportlab` o `kaleido`, instalalos con "
               "`pip install streamlit-folium reportlab kaleido`.")
