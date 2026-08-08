"""Tema de gráficos.

Un único lugar define tipografía, grillas, colores y comportamiento del hover.
Los módulos de gráficos sólo eligen la forma; nunca eligen un color a mano.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

from ..config import DARK, LIGHT, Palette

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def build_template(p: Palette) -> go.layout.Template:
    return go.layout.Template(
        layout=go.Layout(
            colorway=list(p.series),
            font=dict(family=FONT, size=13, color=p.text_primary),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=56, r=24, t=56, b=48),
            title=dict(font=dict(size=17, color=p.text_primary), x=0, xanchor="left", y=0.97),
            hovermode="x unified",
            hoverlabel=dict(
                bgcolor=p.surface, bordercolor=p.axis, font=dict(family=FONT, size=12,
                                                                color=p.text_primary),
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                font=dict(size=12, color=p.text_secondary), bgcolor="rgba(0,0,0,0)",
                itemsizing="constant",
            ),
            xaxis=dict(
                showgrid=False, zeroline=False, linecolor=p.axis, linewidth=1,
                ticks="outside", tickcolor=p.axis, ticklen=4,
                tickfont=dict(size=11, color=p.muted), title=dict(font=dict(size=12, color=p.text_secondary)),
                showspikes=True, spikemode="across", spikethickness=1, spikedash="dot",
                spikecolor=p.muted,
            ),
            yaxis=dict(
                showgrid=True, gridcolor=p.grid, gridwidth=1, zeroline=False,
                linecolor="rgba(0,0,0,0)", ticks="", tickfont=dict(size=11, color=p.muted),
                title=dict(font=dict(size=12, color=p.text_secondary)),
            ),
            colorscale=dict(sequential=_seq_scale(), diverging=_div_scale(p)),
        ),
        data=dict(
            scatter=[go.Scatter(line=dict(width=2), marker=dict(size=8))],
            bar=[go.Bar(marker=dict(line=dict(width=0)))],
        ),
    )


def _seq_scale() -> list[list]:
    from ..config import SEQ_BLUE

    n = len(SEQ_BLUE) - 1
    return [[i / n, c] for i, c in enumerate(SEQ_BLUE)]


def _div_scale(p: Palette) -> list[list]:
    from ..config import DIV_ANOM_DARK, DIV_ANOM_LIGHT

    colors = DIV_ANOM_DARK if p is DARK else DIV_ANOM_LIGHT
    n = len(colors) - 1
    return [[i / n, c] for i, c in enumerate(colors)]


TEMPLATE_LIGHT = build_template(LIGHT)
TEMPLATE_DARK = build_template(DARK)
pio.templates["agrolens_light"] = TEMPLATE_LIGHT
pio.templates["agrolens_dark"] = TEMPLATE_DARK


def template_name(dark: bool) -> str:
    return "agrolens_dark" if dark else "agrolens_light"


def pal(dark: bool) -> Palette:
    return DARK if dark else LIGHT


def ramp_to_scale(colors: list[str] | tuple[str, ...]) -> list[list]:
    """Convierte una rampa discreta en escala continua para heatmaps."""
    n = len(colors) - 1
    return [[i / n, c] for i, c in enumerate(colors)]


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
