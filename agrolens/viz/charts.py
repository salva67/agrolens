"""Gráficos.

Reglas que se respetan en todo el módulo:
  * un solo eje Y por panel — nunca dos escalas superpuestas; cuando hacen
    falta dos magnitudes, van en paneles apilados que comparten el eje X;
  * el color identifica a la entidad, nunca a su posición en un ranking;
  * leyenda siempre que haya dos o más series, y etiquetas directas selectivas;
  * marcas finas, grilla discreta y hover con crosshair.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..config import DIV_ANOM_DARK, DIV_ANOM_LIGHT, SEQ_BLUE
from ..indices import get_index
from .theme import hex_to_rgba, pal, ramp_to_scale, template_name

HOVER_DATE = "%d/%m/%Y"


def _fig(dark: bool, height: int = 380, title: str | None = None) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template=template_name(dark), height=height)
    if title:
        fig.update_layout(title=title)
    return fig


# --------------------------------------------------------------------------
# Vegetación
# --------------------------------------------------------------------------
def index_timeseries(series: pd.DataFrame, curve: pd.DataFrame, index_key: str = "NDVI",
                     dark: bool = False, events: list[tuple] | None = None,
                     height: int = 420) -> go.Figure:
    """Curva del índice: banda intra-lote, observaciones y curva suavizada."""
    p = pal(dark)
    idx = get_index(index_key)
    fig = _fig(dark, height, f"{idx.label} — evolución del lote")

    if not series.empty and {"p10", "p90"} <= set(series.columns):
        fig.add_trace(go.Scatter(
            x=list(series["date"]) + list(series["date"][::-1]),
            y=list(series["p90"]) + list(series["p10"][::-1]),
            fill="toself", fillcolor=hex_to_rgba(p.s(2), 0.16), line=dict(width=0),
            name="Rango interno del lote (p10–p90)", hoverinfo="skip",
        ))

    if not curve.empty:
        fig.add_trace(go.Scatter(
            x=curve["date"], y=curve["smooth"], mode="lines", name=f"{idx.label} suavizado",
            line=dict(color=p.s(0), width=2),
            hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.3f}}</b><extra></extra>",
        ))

    if not series.empty:
        fig.add_trace(go.Scatter(
            x=series["date"], y=series["mean"], mode="markers", name="Observación satelital",
            marker=dict(color=p.s(0), size=8, line=dict(color=p.surface, width=2)),
            customdata=np.stack([series["valid_fraction"] * 100, series["cloud_scene_pct"]], axis=-1),
            hovertemplate=(f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.3f}}</b>"
                           "<br>Píxeles válidos: %{customdata[0]:.0f} %"
                           "<br>Nubosidad de escena: %{customdata[1]:.0f} %<extra></extra>"),
        ))

    for ev in events or []:
        when, label, color = ev
        fig.add_vline(x=when, line=dict(color=color, width=1, dash="dot"))
        fig.add_annotation(x=when, y=1, yref="paper", text=label, showarrow=False,
                           font=dict(size=11, color=p.text_secondary), yshift=-6, xshift=4,
                           xanchor="left")

    fig.update_yaxes(title=idx.label, range=[max(-0.1, idx.vmin - 0.05), idx.vmax + 0.03])
    fig.update_xaxes(title=None)
    return fig


def index_comparison(curves: dict[str, pd.DataFrame], dark: bool = False,
                     height: int = 380) -> go.Figure:
    """Varios índices normalizados 0–1 en un mismo panel (un solo eje)."""
    p = pal(dark)
    fig = _fig(dark, height, "Índices normalizados — misma escala, distinto significado")
    for i, (key, curve) in enumerate(curves.items()):
        if curve.empty:
            continue
        idx = get_index(key)
        y = (curve["smooth"] - idx.vmin) / (idx.vmax - idx.vmin)
        fig.add_trace(go.Scatter(
            x=curve["date"], y=y.clip(0, 1), mode="lines", name=idx.label,
            line=dict(color=p.s(i), width=2),
            hovertemplate=f"{idx.label}: %{{y:.2f}} (normalizado)<extra></extra>",
        ))
    fig.update_yaxes(title="Valor normalizado (0–1)", range=[0, 1.02])
    return fig


def uniformity_chart(series: pd.DataFrame, dark: bool = False, height: int = 300) -> go.Figure:
    """Uniformidad del lote a lo largo del tiempo (100 = parejo)."""
    p = pal(dark)
    fig = _fig(dark, height, "Uniformidad interna del lote")
    fig.add_trace(go.Scatter(
        x=series["date"], y=series["uniformity"], mode="lines+markers",
        name="Uniformidad", line=dict(color=p.s(6), width=2), marker=dict(size=7),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.0f}} / 100</b><extra></extra>",
    ))
    fig.add_hline(y=70, line=dict(color=p.muted, width=1, dash="dot"))
    fig.add_annotation(x=1, xref="paper", y=70, text="umbral de lote parejo", showarrow=False,
                       font=dict(size=11, color=p.muted), xanchor="right", yshift=8)
    fig.update_yaxes(title="Índice de uniformidad", range=[0, 105])
    return fig


def history_envelope(env: pd.DataFrame, rank: pd.DataFrame, index_key: str = "NDVI",
                     dark: bool = False, height: int = 420) -> go.Figure:
    """Campaña actual dentro de la banda de las campañas anteriores."""
    p = pal(dark)
    idx = get_index(index_key)
    fig = _fig(dark, height, f"{idx.label} — campaña actual contra la historia del lote")

    if not env.empty:
        fig.add_trace(go.Scatter(
            x=list(env["das"]) + list(env["das"][::-1]),
            y=list(env["p10"]) + list(env["p90"][::-1]), fill="toself",
            fillcolor=hex_to_rgba(p.muted, 0.14), line=dict(width=0),
            name="Rango histórico p10–p90", hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=list(env["das"]) + list(env["das"][::-1]),
            y=list(env["p25"]) + list(env["p75"][::-1]), fill="toself",
            fillcolor=hex_to_rgba(p.muted, 0.22), line=dict(width=0),
            name="Rango histórico p25–p75", hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=env["das"], y=env["p50"], mode="lines", name="Mediana histórica",
            line=dict(color=p.muted, width=2, dash="dash"),
            hovertemplate="Día %{x}<br>Mediana: %{y:.3f}<extra></extra>",
        ))

    if not rank.empty:
        fig.add_trace(go.Scatter(
            x=rank["das"], y=rank["valor"], mode="lines", name="Campaña actual",
            line=dict(color=p.s(0), width=3),
            customdata=rank["percentil"],
            hovertemplate="Día %{x}<br><b>%{y:.3f}</b><br>Percentil histórico: "
                          "%{customdata:.0f}<extra></extra>",
        ))
    fig.update_xaxes(title="Días desde la siembra")
    fig.update_yaxes(title=idx.label)
    return fig


def anomaly_bars(rank: pd.DataFrame, dark: bool = False, height: int = 300) -> go.Figure:
    """Desvío respecto de la mediana histórica, día a día."""
    p = pal(dark)
    fig = _fig(dark, height, "Anomalía respecto de la mediana histórica")
    if rank.empty:
        return fig
    colors = [p.s(0) if v >= 0 else p.critical for v in rank["anomalia"]]
    fig.add_trace(go.Bar(
        x=rank["date"], y=rank["anomalia"], marker=dict(color=colors), name="Anomalía",
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:+.3f}}</b><extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color=p.axis, width=1))
    fig.update_yaxes(title="Diferencia contra la mediana")
    fig.update_layout(showlegend=False)
    return fig


# --------------------------------------------------------------------------
# Clima y agua
# --------------------------------------------------------------------------
def rain_panel(wx: pd.DataFrame, dark: bool = False, height: int = 460,
               climatology: bool = True) -> go.Figure:
    """Lluvia diaria y acumulada en dos paneles con eje X compartido.

    Deliberadamente NO se superponen en un eje doble: son magnitudes distintas
    y el eje doble hace que cualquier cruce de líneas parezca significativo.
    """
    p = pal(dark)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.09,
                        subplot_titles=("Lluvia diaria (mm)", "Lluvia acumulada (mm)"))
    obs = wx[wx["source"] == "observado"] if "source" in wx else wx
    fc = wx[wx["source"] == "pronóstico"] if "source" in wx else wx.iloc[0:0]

    fig.add_trace(go.Bar(
        x=obs["date"], y=obs["precip_mm"], name="Lluvia observada", marker_color=p.s(0),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.1f}} mm</b><extra></extra>",
    ), row=1, col=1)
    if not fc.empty:
        fig.add_trace(go.Bar(
            x=fc["date"], y=fc["precip_mm"], name="Lluvia pronosticada",
            marker=dict(color=hex_to_rgba(p.s(0), 0.45), line=dict(color=p.s(0), width=1)),
            hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.1f}} mm</b> (pronóstico)<extra></extra>",
        ), row=1, col=1)

    acc = wx.copy()
    acc["acum"] = pd.to_numeric(acc["precip_mm"], errors="coerce").fillna(0).cumsum()
    fig.add_trace(go.Scatter(
        x=acc["date"], y=acc["acum"], name="Acumulado del período", mode="lines",
        line=dict(color=p.s(0), width=2),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.0f}} mm</b><extra></extra>",
    ), row=2, col=1)

    if climatology and "precip_norm" in wx.columns and wx["precip_norm"].notna().any():
        norm = wx.copy()
        norm["acum_norm"] = pd.to_numeric(norm["precip_norm"], errors="coerce").fillna(0).cumsum()
        fig.add_trace(go.Scatter(
            x=norm["date"], y=norm["acum_norm"], name="Normal 1991–2020", mode="lines",
            line=dict(color=p.muted, width=2, dash="dash"),
            hovertemplate="Normal: %{y:.0f} mm<extra></extra>",
        ), row=2, col=1)

    fig.update_layout(template=template_name(dark), height=height, barmode="overlay",
                      title="Régimen de lluvias del lote")
    fig.update_yaxes(title="mm/día", row=1, col=1)
    fig.update_yaxes(title="mm acumulados", row=2, col=1)
    return fig


def water_balance_panel(wb: pd.DataFrame, dark: bool = False, height: int = 480) -> go.Figure:
    """Agua útil del perfil y coeficiente de estrés, en paneles apilados."""
    p = pal(dark)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.10,
                        row_heights=[0.62, 0.38],
                        subplot_titles=("Agua útil almacenada (% de la capacidad)",
                                        "Coeficiente de estrés hídrico (1 = sin estrés)"))
    fig.add_trace(go.Scatter(
        x=wb["date"], y=wb["agua_util_pct"], name="Agua útil", mode="lines",
        line=dict(color=p.s(0), width=2), fill="tozeroy",
        fillcolor=hex_to_rgba(p.s(0), 0.18),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.0f}} %</b><extra></extra>",
    ), row=1, col=1)
    for level, label, color in ((50, "umbral de alerta", p.warning), (25, "estrés severo", p.critical)):
        fig.add_hline(y=level, line=dict(color=color, width=1, dash="dot"), row=1, col=1)
        fig.add_annotation(x=1, xref="paper", y=level, yref="y", text=label, showarrow=False,
                           font=dict(size=11, color=p.text_secondary), xanchor="right", yshift=8)

    fig.add_trace(go.Scatter(
        x=wb["date"], y=wb["ks"], name="Ks", mode="lines", line=dict(color=p.s(1), width=2),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>Ks %{{y:.2f}}</b><extra></extra>",
    ), row=2, col=1)

    fig.update_layout(template=template_name(dark), height=height,
                      title="Balance hídrico del perfil explorado")
    fig.update_yaxes(title="%", range=[0, 105], row=1, col=1)
    fig.update_yaxes(title="Ks", range=[0, 1.05], row=2, col=1)
    return fig


def gdd_chart(gdd: pd.DataFrame, crop, dark: bool = False, height: int = 340) -> go.Figure:
    """Suma térmica acumulada con las etapas fenológicas marcadas."""
    p = pal(dark)
    fig = _fig(dark, height, f"Suma térmica acumulada — {crop.label}")
    fig.add_trace(go.Scatter(
        x=gdd["date"], y=gdd["gdd_acum"], mode="lines", name="Grados-día acumulados",
        line=dict(color=p.s(3), width=2),
        customdata=gdd["etapa"],
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.0f}} °C día</b><br>%{{customdata}}<extra></extra>",
    ))
    for st in crop.stages:
        level = st.gdd_frac * crop.gdd_cycle
        if level <= 0 or level > float(gdd["gdd_acum"].max() or 0) * 1.05:
            continue
        fig.add_hline(y=level, line=dict(color=p.grid, width=1))
        fig.add_annotation(x=0, xref="paper", y=level, text=st.name, showarrow=False,
                           font=dict(size=11, color=p.muted), xanchor="left", yshift=8)
    fig.update_yaxes(title="°C día")
    fig.update_layout(showlegend=False)
    return fig


def temperature_panel(wx: pd.DataFrame, crop, dark: bool = False, height: int = 340) -> go.Figure:
    """Máximas y mínimas con los umbrales de daño del cultivo."""
    p = pal(dark)
    fig = _fig(dark, height, "Temperaturas y umbrales de daño")
    fig.add_trace(go.Scatter(
        x=list(wx["date"]) + list(wx["date"][::-1]),
        y=list(wx["tmax"]) + list(wx["tmin"][::-1]), fill="toself",
        fillcolor=hex_to_rgba(p.s(1), 0.16), line=dict(width=0),
        name="Rango diario", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=wx["date"], y=wx["tmax"], mode="lines", name="Máxima",
        line=dict(color=p.s(1), width=2),
        hovertemplate=f"%{{x|{HOVER_DATE}}}<br>Máxima: <b>%{{y:.1f}} °C</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=wx["date"], y=wx["tmin"], mode="lines", name="Mínima",
        line=dict(color=p.s(0), width=2),
        hovertemplate=f"Mínima: <b>%{{y:.1f}} °C</b><extra></extra>",
    ))
    fig.add_hline(y=crop.heat_critical_c, line=dict(color=p.critical, width=1, dash="dot"))
    fig.add_hline(y=crop.frost_critical_c, line=dict(color=p.s(0), width=1, dash="dot"))
    fig.update_yaxes(title="°C")
    return fig


def rain_calendar(wx: pd.DataFrame, dark: bool = False, height: int = 260) -> go.Figure:
    """Calendario de lluvias: semanas en el eje X, días de la semana en el Y."""
    p = pal(dark)
    d = wx.copy()
    d["dt"] = pd.to_datetime(d["date"])
    d["semana"] = d["dt"].dt.to_period("W").dt.start_time
    d["dow"] = d["dt"].dt.dayofweek
    pivot = d.pivot_table(index="dow", columns="semana", values="precip_mm", aggfunc="sum")
    dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    fig = _fig(dark, height, "Calendario de lluvias")
    fig.add_trace(go.Heatmap(
        z=pivot.values, x=pivot.columns, y=[dias[i] for i in pivot.index],
        colorscale=ramp_to_scale([p.grid, *SEQ_BLUE]),
        xgap=2, ygap=2, colorbar=dict(title="mm", thickness=12, len=0.8),
        hovertemplate="Semana del %{x|%d/%m}<br>%{y}: <b>%{z:.1f} mm</b><extra></extra>",
    ))
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(showspikes=False)
    return fig


# --------------------------------------------------------------------------
# Zonas y rásteres
# --------------------------------------------------------------------------
def raster_map(raster: dict, index_key: str = "NDVI", dark: bool = False,
               height: int = 460, diverging: bool = False, title: str | None = None) -> go.Figure:
    """Ráster del índice recortado al lote."""
    idx = get_index(index_key)
    values = np.asarray(raster["values"], dtype=float)
    if diverging:
        lim = float(np.nanpercentile(np.abs(values), 95) or 0.1)
        vmin, vmax = -lim, lim
        scale = ramp_to_scale(DIV_ANOM_DARK if dark else DIV_ANOM_LIGHT)
    else:
        vmin, vmax = idx.vmin, idx.vmax
        scale = ramp_to_scale(list(idx.ramp))

    fig = _fig(dark, height, title or f"{idx.label} — distribución dentro del lote")
    fig.add_trace(go.Heatmap(
        z=values, colorscale=scale, zmin=vmin, zmax=vmax,
        colorbar=dict(title=idx.label, thickness=14, len=0.85),
        hovertemplate=f"{idx.label}: <b>%{{z:.3f}}</b><extra></extra>",
    ))
    fig.update_layout(hovermode="closest", margin=dict(l=8, r=8, t=48, b=8))
    fig.update_xaxes(visible=False, showspikes=False)
    fig.update_yaxes(visible=False, autorange="reversed", scaleanchor="x", scaleratio=1)
    return fig


def zone_map(zones: dict, dark: bool = False, height: int = 460,
             title: str = "Zonas de manejo") -> go.Figure:
    """Mapa de zonas con colores fijos por clase."""
    labels = np.asarray(zones["labels"], dtype=float)
    labels[labels < 0] = np.nan
    stats = zones["stats"]
    n = len(stats)
    colors = [s.color for s in stats]
    # Escala discreta: cada zona ocupa un tramo igual
    scale = []
    for i, c in enumerate(colors):
        scale.append([i / n, c])
        scale.append([(i + 1) / n, c])

    fig = _fig(dark, height, title)
    fig.add_trace(go.Heatmap(
        z=labels, colorscale=scale, zmin=-0.5, zmax=n - 0.5, showscale=False,
        customdata=np.where(np.isnan(labels), "", labels),
        hovertemplate="Zona %{z}<extra></extra>",
    ))
    fig.update_layout(hovermode="closest", margin=dict(l=8, r=8, t=48, b=8))
    fig.update_xaxes(visible=False, showspikes=False)
    fig.update_yaxes(visible=False, autorange="reversed", scaleanchor="x", scaleratio=1)
    return fig


def zone_bars(stats, dark: bool = False, height: int = 320,
              value_label: str = "Índice medio") -> go.Figure:
    """Superficie e índice medio por zona."""
    p = pal(dark)
    fig = _fig(dark, height, "Superficie por ambiente")
    labels = [f"{s.label}" for s in stats]
    fig.add_trace(go.Bar(
        x=[s.area_ha for s in stats], y=labels, orientation="h",
        marker=dict(color=[s.color for s in stats],
                    line=dict(color=p.surface, width=2)),
        text=[f"{s.area_ha:.1f} ha · {s.pct:.0f} %" for s in stats],
        textposition="outside", textfont=dict(color=p.text_secondary, size=12),
        customdata=[[s.mean, s.pct] for s in stats],
        hovertemplate="<b>%{y}</b><br>%{x:.1f} ha (%{customdata[1]:.0f} %)"
                      f"<br>{value_label}: %{{customdata[0]:.3f}}<extra></extra>",
    ))
    fig.update_layout(showlegend=False, hovermode="closest",
                      margin=dict(l=8, r=90, t=48, b=32))
    fig.update_xaxes(title="hectáreas", showgrid=True, gridcolor=p.grid)
    fig.update_yaxes(autorange="reversed")
    return fig


def distribution(values: np.ndarray, index_key: str = "NDVI", dark: bool = False,
                 height: int = 300, reference: float | None = None) -> go.Figure:
    """Histograma de los píxeles del lote: muestra colas y bimodalidad."""
    p = pal(dark)
    idx = get_index(index_key)
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    fig = _fig(dark, height, f"Distribución interna del {idx.label}")
    fig.add_trace(go.Histogram(
        x=v, nbinsx=48, marker=dict(color=p.s(0), line=dict(color=p.surface, width=1)),
        name="Píxeles", hovertemplate=f"{idx.label} %{{x:.2f}}<br>%{{y}} píxeles<extra></extra>",
    ))
    if reference is not None:
        fig.add_vline(x=reference, line=dict(color=p.text_secondary, width=2, dash="dash"))
        fig.add_annotation(x=reference, y=1, yref="paper", text="media del lote", showarrow=False,
                           font=dict(size=11, color=p.text_secondary), xanchor="left", xshift=6)
    fig.update_layout(showlegend=False, hovermode="closest")
    fig.update_xaxes(title=idx.label)
    fig.update_yaxes(title="píxeles")
    return fig


def season_comparison(history: pd.DataFrame, current: pd.DataFrame | None = None,
                      dark: bool = False, height: int = 420,
                      index_key: str = "NDVI") -> go.Figure:
    """Todas las campañas superpuestas por días desde la siembra."""
    p = pal(dark)
    idx = get_index(index_key)
    fig = _fig(dark, height, f"{idx.label} por campaña")
    campañas = sorted(history["campaña"].unique()) if not history.empty else []
    for i, c in enumerate(campañas[:7]):
        sub = history[history["campaña"] == c]
        fig.add_trace(go.Scatter(
            x=sub["das"], y=sub["smooth"], mode="lines", name=c,
            line=dict(color=p.s(i + 1), width=2),
            hovertemplate=f"{c}<br>Día %{{x}}: <b>%{{y:.3f}}</b><extra></extra>",
        ))
    if current is not None and not current.empty:
        fig.add_trace(go.Scatter(
            x=current["das"], y=current["valor"], mode="lines", name="Actual",
            line=dict(color=p.s(0), width=3),
            hovertemplate="Actual<br>Día %{x}: <b>%{y:.3f}</b><extra></extra>",
        ))
    fig.update_xaxes(title="Días desde la siembra")
    fig.update_yaxes(title=idx.label)
    return fig


def prescription_chart(presc: pd.DataFrame, dark: bool = False, height: int = 320) -> go.Figure:
    """Dosis por ambiente."""
    p = pal(dark)
    dose_col = [c for c in presc.columns if c.startswith("dosis_")][0]
    unit = dose_col.replace("dosis_", "").replace("_", "/")
    fig = _fig(dark, height, f"Prescripción por ambiente ({unit})")
    fig.add_trace(go.Bar(
        x=presc["etiqueta"], y=presc[dose_col],
        marker=dict(color=list(presc["color"]), line=dict(color=p.surface, width=2)),
        text=[f"{v:.0f}" for v in presc[dose_col]], textposition="outside",
        textfont=dict(color=p.text_secondary, size=12),
        customdata=np.stack([presc["superficie_ha"], presc["insumo_total"]], axis=-1),
        hovertemplate="<b>%{x}</b><br>Dosis: %{y:.0f} " + unit +
                      "<br>Superficie: %{customdata[0]:.1f} ha"
                      "<br>Insumo total: %{customdata[1]:.0f}<extra></extra>",
    ))
    fig.update_layout(showlegend=False, hovermode="closest")
    fig.update_yaxes(title=unit)
    return fig


def storm_timeline(storms: pd.DataFrame, wx: pd.DataFrame, dark: bool = False,
                   height: int = 420) -> go.Figure:
    """Ráfagas diarias con los eventos de tormenta marcados, y su severidad debajo."""
    p = pal(dark)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.10,
                        row_heights=[0.6, 0.4],
                        subplot_titles=("Ráfaga máxima diaria (km/h)",
                                        "Severidad del evento (0–100)"))

    if wx is not None and not wx.empty and "gust_kmh" in wx.columns:
        fig.add_trace(go.Scatter(
            x=wx["date"], y=pd.to_numeric(wx["gust_kmh"], errors="coerce"),
            mode="lines", name="Ráfaga", line=dict(color=p.s(0), width=1.6),
            hovertemplate=f"%{{x|{HOVER_DATE}}}<br><b>%{{y:.0f}} km/h</b><extra></extra>",
        ), row=1, col=1)

    from ..analytics.storms import RAFAGA_DANO, RAFAGA_SEVERA

    for nivel, etiqueta, color in ((RAFAGA_DANO, "daño probable", p.warning),
                                   (RAFAGA_SEVERA, "daño severo", p.critical)):
        fig.add_hline(y=nivel, line=dict(color=color, width=1, dash="dot"), row=1, col=1)
        fig.add_annotation(x=1, xref="paper", y=nivel, yref="y", text=etiqueta, showarrow=False,
                           font=dict(size=11, color=p.text_secondary), xanchor="right", yshift=8)

    if storms is not None and not storms.empty:
        granizo = storms[storms["granizo"]]
        resto = storms[~storms["granizo"]]
        for sub, nombre, color in ((resto, "Tormenta", p.s(1)),
                                   (granizo, "Con granizo", p.critical)):
            if sub.empty:
                continue
            fig.add_trace(go.Bar(
                x=sub["date"], y=sub["severidad"], name=nombre, marker_color=color,
                customdata=sub["tipo"],
                hovertemplate=f"%{{x|{HOVER_DATE}}}<br>Severidad <b>%{{y}}</b>"
                              "<br>%{customdata}<extra></extra>",
            ), row=2, col=1)

    fig.update_layout(template=template_name(dark), height=height,
                      title="Tormentas y viento sobre el lote", barmode="stack")
    fig.update_yaxes(title="km/h", row=1, col=1)
    fig.update_yaxes(title="severidad", range=[0, 105], row=2, col=1)
    return fig


def coverage_chart(quality: pd.DataFrame, dark: bool = False, height: int = 280) -> go.Figure:
    """Observaciones válidas por mes: la salud del monitoreo satelital."""
    p = pal(dark)
    fig = _fig(dark, height, "Observaciones satelitales válidas por mes")
    fig.add_trace(go.Bar(
        x=quality["mes"], y=quality["observaciones"], marker_color=p.s(2),
        customdata=quality["cobertura_media"] * 100,
        hovertemplate="%{x|%m/%Y}<br><b>%{y} imágenes</b>"
                      "<br>Cobertura media: %{customdata:.0f} %<extra></extra>",
    ))
    fig.update_layout(showlegend=False, hovermode="closest")
    fig.update_yaxes(title="imágenes")
    return fig
