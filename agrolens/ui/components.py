"""Piezas de interfaz reutilizables.

Toda la identidad visual de la app vive acá: si algo se ve distinto en dos
páginas, es un error, no una decisión.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import streamlit as st

from ..config import APP_NAME, APP_TAGLINE, APP_VERSION, DARK, LIGHT
from ..models import SEVERITY_ICON, SEVERITY_LABEL, Alert, AnalysisConfig, Lote

# --------------------------------------------------------------------------
# Configuración de página y estilos
# --------------------------------------------------------------------------
CSS = """
<style>
:root {
  --al-radius: 12px;
  --al-border: rgba(128,128,128,.22);
}
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1440px; }
h1, h2, h3 { letter-spacing: -.01em; }

.al-hero { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap;
           margin-bottom:.2rem; }
.al-hero h1 { margin:0; font-size:1.9rem; font-weight:700; }
.al-hero span { color:#898781; font-size:.95rem; }

.al-cards { display:grid; gap:.7rem; grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
            margin:.6rem 0 1.1rem; }
.al-card { border:1px solid var(--al-border); border-radius:var(--al-radius);
           padding:.75rem .9rem; background:rgba(128,128,128,.045); }
.al-card .v { font-size:1.55rem; font-weight:700; line-height:1.15;
              font-variant-numeric:tabular-nums; }
.al-card .l { font-size:.72rem; color:#898781; text-transform:uppercase;
              letter-spacing:.04em; margin-top:.15rem; }
.al-card .d { font-size:.78rem; margin-top:.25rem; }

.al-alert { border:1px solid var(--al-border); border-left-width:4px;
            border-radius:var(--al-radius); padding:.7rem .9rem; margin-bottom:.55rem;
            background:rgba(128,128,128,.045); }
.al-alert .t { font-weight:650; font-size:.96rem; }
.al-alert .m { font-size:.86rem; opacity:.9; margin-top:.2rem; }
.al-alert .r { font-size:.86rem; margin-top:.35rem; }
.al-alert .tag { font-size:.68rem; text-transform:uppercase; letter-spacing:.05em;
                 color:#898781; margin-left:.4rem; }

.al-chip { display:inline-block; padding:.12rem .5rem; border-radius:999px;
           border:1px solid var(--al-border); font-size:.74rem; margin-right:.3rem; }
.al-note { font-size:.8rem; color:#898781; margin-top:-.4rem; margin-bottom:.8rem; }
.al-legend { display:flex; gap:.9rem; flex-wrap:wrap; font-size:.8rem; margin:.3rem 0 .6rem; }
.al-legend i { width:12px; height:12px; border-radius:3px; display:inline-block;
               margin-right:.35rem; vertical-align:-1px; }
div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
</style>
"""


def page_setup(title: str, icon: str = "🌱", wide: bool = True) -> None:
    st.set_page_config(page_title=f"{title} · {APP_NAME}", page_icon=icon,
                       layout="wide" if wide else "centered",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)


def is_dark() -> bool:
    """Detecta el tema activo para que los gráficos acompañen a la interfaz."""
    try:
        theme = getattr(st.context, "theme", None)
        if theme is not None and getattr(theme, "type", None):
            return theme.type == "dark"
    except Exception:
        pass
    try:
        return (st.get_option("theme.base") or "light") == "dark"
    except Exception:
        return False


def palette():
    return DARK if is_dark() else LIGHT


def hero(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="al-hero"><h1>{title}</h1><span>{subtitle}</span></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="al-note">{text}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Tarjetas y alertas
# --------------------------------------------------------------------------
def cards(items: Iterable[tuple]) -> None:
    """Fila de tarjetas: (valor, etiqueta[, detalle][, color])."""
    html = ['<div class="al-cards">']
    for item in items:
        value, label = item[0], item[1]
        detail = item[2] if len(item) > 2 else ""
        color = item[3] if len(item) > 3 else None
        style = f' style="color:{color}"' if color else ""
        html.append(
            f'<div class="al-card"><div class="v"{style}>{value}</div>'
            f'<div class="l">{label}</div>'
            + (f'<div class="d">{detail}</div>' if detail else "")
            + "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def alert_cards(alerts: list[Alert], limit: int = 12, empty: str | None = None) -> None:
    if not alerts:
        st.success(empty or "Sin hallazgos relevantes en el período analizado.")
        return
    p = palette()
    colors = {"critical": p.critical, "serious": p.serious, "warning": p.warning,
              "info": p.s(0), "good": p.good}
    for a in alerts[:limit]:
        c = colors.get(a.severity, p.muted)
        rec = (f'<div class="r"><b>Qué hacer:</b> {a.recommendation}</div>'
               if a.recommendation else "")
        st.markdown(
            f'<div class="al-alert" style="border-left-color:{c}">'
            f'<div class="t">{SEVERITY_ICON.get(a.severity, "")} {a.title}'
            f'<span class="tag">{SEVERITY_LABEL.get(a.severity, "")} · {a.source}</span></div>'
            f'<div class="m">{a.detail}</div>{rec}</div>',
            unsafe_allow_html=True,
        )


def legend(items: list[tuple[str, str]]) -> None:
    html = ['<div class="al-legend">']
    for label, color in items:
        html.append(f'<span><i style="background:{color}"></i>{label}</span>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def delta_color(value: float, good_is_up: bool = True) -> str:
    p = palette()
    if abs(value) < 1e-9:
        return p.text_secondary
    up = value > 0
    return p.good if up == good_is_up else p.critical


# --------------------------------------------------------------------------
# Estado de la sesión
# --------------------------------------------------------------------------
def init_state() -> None:
    st.session_state.setdefault("lote_id", None)
    st.session_state.setdefault("draft_geometry", None)
    st.session_state.setdefault("results", {})
    st.session_state.setdefault("config", None)
    st.session_state.setdefault("demo_mode", False)


def current_user():
    """Usuario de la sesión. La puerta de acceso ya corrió en `app.py`."""
    from .auth import User, current_user as _cu

    return _cu() or User(email="local", name="Sesión local", modo="local")


def current_lote() -> Lote | None:
    """Lote activo del usuario. Si no hay ninguno elegido, toma el primero."""
    from .. import storage

    init_state()
    me = current_user().email
    lid = st.session_state.get("lote_id")
    if lid:
        lote = storage.get_lote(lid, me)
        if lote:
            return lote
        st.session_state["lote_id"] = None  # dejó de tener acceso
    guardados = storage.list_lotes(me)
    if guardados:
        st.session_state["lote_id"] = guardados[0].id
        return guardados[0]
    return None


def require_lote() -> Lote:
    """Devuelve el lote activo o corta la página con un mensaje útil."""
    lote = current_lote()
    if lote is None:
        st.info("Primero elegí o dibujá un lote en la página **Lotes**.", icon="🗺️")
        if st.button("Ir a Lotes", type="primary"):
            st.switch_page("views/1_lotes.py")
        st.stop()
    return lote  # type: ignore[return-value]


def default_config(lote: Lote) -> AnalysisConfig:
    """Ventana por defecto: la campaña en curso si hay fecha de siembra."""
    from datetime import timedelta

    from ..crops import get_crop

    crop = get_crop(lote.crop)
    if lote.sowing_date:
        start = lote.sowing_date - timedelta(days=20)
        end = min(date.today(), lote.sowing_date + timedelta(days=crop.cycle_days + 30))
        if end <= start:
            end = date.today()
    else:
        start, end = date.today() - timedelta(days=180), date.today()
    return AnalysisConfig(start=start, end=end)


def get_config(lote: Lote) -> AnalysisConfig:
    cfg = st.session_state.get("config")
    if not isinstance(cfg, AnalysisConfig):
        cfg = default_config(lote)
        st.session_state["config"] = cfg
    return cfg


def result_key(lote: Lote, cfg: AnalysisConfig, flags: tuple) -> str:
    return f"{lote.id}:{lote.geom_hash}:{cfg.cache_key}:{flags}"


def get_result(lote: Lote, cfg: AnalysisConfig, *, raster: bool = True,
               history: bool = False, weather: bool = True, force: bool = False):
    """Ejecuta el pipeline o devuelve el resultado ya calculado en esta sesión."""
    from .. import pipeline

    init_state()
    flags = (raster, history, weather, st.session_state.get("demo_mode", False))
    key = result_key(lote, cfg, flags)
    if not force and key in st.session_state["results"]:
        return st.session_state["results"][key]

    bar = st.progress(0.0, text="Preparando el análisis…")

    def on_progress(pct: float, msg: str) -> None:
        bar.progress(min(1.0, max(0.0, pct)), text=msg)

    try:
        res = pipeline.run(
            lote, cfg, include_raster=raster, include_history=history,
            include_weather=weather, demo_mode=st.session_state.get("demo_mode") or None,
            progress=on_progress,
        )
    finally:
        bar.empty()

    st.session_state["results"][key] = res
    return res


def clear_results() -> None:
    st.session_state["results"] = {}


# --------------------------------------------------------------------------
# Barra lateral común
# --------------------------------------------------------------------------
def sidebar(lote: Lote | None = None, cfg: AnalysisConfig | None = None,
            show_index: bool = True) -> AnalysisConfig | None:
    """Contexto y controles compartidos por todas las páginas de análisis."""
    from ..crops import get_crop
    from ..indices import FAMILY_LABELS, INDICES, MAP_INDEX_ORDER

    with st.sidebar:
        st.markdown(f"### {APP_NAME}")
        st.caption(APP_TAGLINE)

        if lote is not None:
            crop = get_crop(lote.crop)
            st.markdown(
                f'<span class="al-chip">{lote.area_ha:.1f} ha</span>'
                f'<span class="al-chip">{crop.label}</span>'
                + (f'<span class="al-chip">siembra {lote.sowing_date:%d/%m}</span>'
                   if lote.sowing_date else ""),
                unsafe_allow_html=True,
            )
            st.markdown(f"**{lote.name}**" + (f"  \n{lote.farm}" if lote.farm else ""))

        if cfg is not None:
            st.divider()
            st.markdown("**Período de análisis**")
            c1, c2 = st.columns(2)
            start = c1.date_input("Desde", cfg.start, format="DD/MM/YYYY", key="cfg_start")
            end = c2.date_input("Hasta", cfg.end, format="DD/MM/YYYY", key="cfg_end")

            index_key = cfg.index
            if show_index:
                options = MAP_INDEX_ORDER
                index_key = st.selectbox(
                    "Índice", options, index=options.index(cfg.index) if cfg.index in options else 0,
                    format_func=lambda k: f"{INDICES[k].label}",
                    help="Todos los índices están disponibles: no hay funciones bloqueadas.",
                )
                idx = INDICES[index_key]
                st.caption(f"**{FAMILY_LABELS.get(idx.family, idx.family)}** · {idx.summary}")

            with st.expander("Ajustes avanzados"):
                cloud = st.slider("Nubosidad máxima de escena (%)", 10, 100,
                                  int(cfg.cloud_pct), 5)
                valid = st.slider("Píxeles válidos mínimos en el lote (%)", 20, 100,
                                  int(cfg.min_valid_fraction * 100), 5)
                smooth = st.slider("Ventana de suavizado (días)", 7, 45,
                                   int(cfg.smoothing_days), 2)
                n_zones = st.slider("Cantidad de ambientes", 2, 5, int(cfg.n_zones))
                years = st.slider("Campañas de historia", 2, 10, int(cfg.history_years))

            new_cfg = AnalysisConfig(
                start=start, end=end, index=index_key, cloud_pct=float(cloud),
                min_valid_fraction=valid / 100, n_zones=int(n_zones),
                history_years=int(years), smoothing_days=int(smooth),
            )
            if new_cfg != cfg:
                st.session_state["config"] = new_cfg
            cfg = new_cfg

        st.divider()
        st.session_state["demo_mode"] = st.toggle(
            "Modo demostración", value=st.session_state.get("demo_mode", False),
            help="Datos sintéticos, sin conexión a Earth Engine. Útil para probar la app.",
        )
        st.caption(f"v{APP_VERSION}")

    from .auth import sidebar_account

    sidebar_account(current_user())
    return cfg


def data_source_badge(res: Any) -> None:
    """Aclara siempre de dónde salieron los datos que se están mirando."""
    if res.modo_demo:
        st.warning(
            "**Modo demostración**: los datos son sintéticos y no corresponden a este lote.",
            icon="🧪",
        )
    for aviso in res.avisos:
        st.info(aviso, icon="ℹ️")


def download_buttons(res: Any) -> None:
    from ..report import exports
    from ..report.exports import _slug as slug

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "Excel del análisis", exports.excel_workbook(res),
            file_name=f"{slug(res.lote.name)}_analisis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "GeoJSON del lote", exports.geojson_bytes(res.lote.geometry),
            file_name=f"{slug(res.lote.name)}_lote.geojson",
            mime="application/geo+json", use_container_width=True,
        )
    with c3:
        if res.zonas:
            st.download_button(
                "Paquete de ambientes", exports.zones_package(res),
                file_name=f"{slug(res.lote.name)}_ambientes.zip",
                mime="application/zip", use_container_width=True,
            )
        else:
            st.button("Paquete de ambientes", disabled=True, use_container_width=True,
                      help="Requiere una zonificación calculada.")


def chart(fig, key: str | None = None) -> None:
    st.plotly_chart(fig, use_container_width=True, key=key,
                    config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def table_view(df, label: str = "Ver los datos en tabla") -> None:
    """La alternativa textual al gráfico: obligatoria por accesibilidad."""
    with st.expander(label):
        st.dataframe(df, use_container_width=True, hide_index=True)
