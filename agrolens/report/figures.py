"""Gráficos del informe PDF, dibujados con matplotlib.

Los gráficos de pantalla son de Plotly, que es interactivo. Para el PDF hace
falta un PNG, y la exportación de Plotly depende de `kaleido`, que en Windows
se cuelga con cierta frecuencia y no traía forma de acotar el tiempo. Un
informe que nunca termina es peor que uno sin gráficos, así que la versión
impresa se dibuja con matplotlib: sin binarios externos y sin sorpresas.

Los datos y la paleta son los mismos que en pantalla; sólo cambia el motor de
dibujo.
"""

from __future__ import annotations

import io
import logging
from datetime import date

import matplotlib

matplotlib.use("Agg")  # sin ventana: obligatorio en servidor

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from ..config import LIGHT  # noqa: E402
from ..indices import get_index  # noqa: E402

log = logging.getLogger(__name__)

P = LIGHT
DPI = 170
FONT = {"family": "DejaVu Sans", "size": 9}

plt.rcParams.update({
    "font.family": FONT["family"],
    "font.size": FONT["size"],
    "axes.edgecolor": P.axis,
    "axes.labelcolor": P.text_secondary,
    "axes.titlecolor": P.text_primary,
    "axes.titlesize": 10.5,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": P.grid,
    "grid.linewidth": 0.7,
    "xtick.color": P.muted,
    "ytick.color": P.muted,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.frameon": False,
    "legend.fontsize": 8,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


def _finish(fig) -> bytes:
    """Cierra la figura y devuelve el PNG."""
    import warnings

    buf = io.BytesIO()
    with warnings.catch_warnings():
        # las figuras con leyenda o barra de color fuera de los ejes avisan que
        # tight_layout puede no ser exacto; el recorte final lo hace savefig
        warnings.simplefilter("ignore", UserWarning)
        fig.tight_layout(pad=0.8)
        fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _clean(ax, x_dates: bool = True) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.grid(axis="x", visible=False)
    if x_dates:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))


def _legend_above(ax, ncol: int = 3) -> None:
    """Leyenda en una fila, justo encima del área de datos y debajo del título."""
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.005), ncol=ncol, borderaxespad=0)


def _veg_cmap(ramp: list[str]) -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list("veg", list(ramp), N=256)


def _dates(values) -> list:
    return [pd.Timestamp(v).to_pydatetime() for v in values]


# --------------------------------------------------------------------------
# Vegetación
# --------------------------------------------------------------------------
def index_timeseries(series: pd.DataFrame, curve: pd.DataFrame, index_key: str,
                     events: list[tuple] | None = None, size=(9.2, 3.4)) -> bytes:
    idx = get_index(index_key)
    fig, ax = plt.subplots(figsize=size)

    if not series.empty and {"p10", "p90"} <= set(series.columns):
        ax.fill_between(_dates(series["date"]), series["p10"], series["p90"],
                        color=P.s(2), alpha=0.16, linewidth=0,
                        label="Rango interno del lote (p10–p90)")
    if not curve.empty:
        col = "smooth" if "smooth" in curve.columns else "value"
        ax.plot(_dates(curve["date"]), curve[col], color=P.s(0), linewidth=2,
                label=f"{idx.label} suavizado")
    if not series.empty:
        ax.plot(_dates(series["date"]), series["mean"], "o", markersize=4.5,
                color=P.s(0), markeredgecolor="white", markeredgewidth=0.8,
                linestyle="none", label="Observación satelital")

    for when, label, _color in events or []:
        ax.axvline(pd.Timestamp(when), color=P.muted, linewidth=0.9, linestyle=":")
        ax.annotate(label, (pd.Timestamp(when), ax.get_ylim()[1]), xytext=(3, -10),
                    textcoords="offset points", fontsize=7.5, color=P.text_secondary)

    ax.set_title(f"{idx.label} — evolución del lote", pad=26)
    ax.set_ylabel(idx.label)
    _clean(ax)
    _legend_above(ax, ncol=3)
    return _finish(fig)


def history_envelope(env: pd.DataFrame, rank: pd.DataFrame, index_key: str,
                     size=(9.2, 3.4)) -> bytes:
    idx = get_index(index_key)
    fig, ax = plt.subplots(figsize=size)
    if not env.empty:
        ax.fill_between(env["das"], env["p10"], env["p90"], color=P.muted, alpha=0.14,
                        linewidth=0, label="Rango histórico p10–p90")
        ax.fill_between(env["das"], env["p25"], env["p75"], color=P.muted, alpha=0.22,
                        linewidth=0, label="Rango histórico p25–p75")
        ax.plot(env["das"], env["p50"], color=P.muted, linewidth=1.6, linestyle="--",
                label="Mediana histórica")
    if not rank.empty:
        ax.plot(rank["das"], rank["valor"], color=P.s(0), linewidth=2.4,
                label="Campaña actual")
    ax.set_title(f"{idx.label} — campaña actual contra la historia del lote", pad=26)
    ax.set_xlabel("Días desde la siembra")
    ax.set_ylabel(idx.label)
    _clean(ax, x_dates=False)
    _legend_above(ax, ncol=4)
    return _finish(fig)


def raster_map(raster: dict, index_key: str, size=(6.4, 5.2)) -> bytes:
    idx = get_index(index_key)
    values = np.asarray(raster["values"], dtype=float)
    fig, ax = plt.subplots(figsize=size)
    im = ax.imshow(values, cmap=_veg_cmap(list(idx.ramp)), vmin=idx.vmin, vmax=idx.vmax,
                   interpolation="nearest")
    ax.set_title(f"{idx.label} dentro del lote")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.045, pad=0.02)
    cb.set_label(idx.label, fontsize=8, color=P.text_secondary)
    cb.outline.set_visible(False)
    return _finish(fig)


def zone_map(zones: dict, title: str = "Ambientes del lote", size=(6.4, 5.2)) -> bytes:
    labels = np.asarray(zones["labels"], dtype=float)
    labels[labels < 0] = np.nan
    stats = zones["stats"]
    cmap = ListedColormap([s.color for s in stats])
    fig, ax = plt.subplots(figsize=size)
    ax.imshow(labels, cmap=cmap, vmin=-0.5, vmax=len(stats) - 0.5, interpolation="nearest")
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.legend(handles=[Patch(facecolor=s.color, label=f"{s.label} · {s.area_ha:.1f} ha")
                       for s in stats],
              loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2)
    return _finish(fig)


def zone_bars(stats, value_label: str = "Índice medio", size=(6.4, 3.0)) -> bytes:
    fig, ax = plt.subplots(figsize=size)
    y = np.arange(len(stats))[::-1]
    ax.barh(y, [s.area_ha for s in stats], color=[s.color for s in stats],
            edgecolor="white", linewidth=1.5, height=0.72)
    for yi, s in zip(y, stats):
        ax.annotate(f"{s.area_ha:.1f} ha · {s.pct:.0f} %", (s.area_ha, yi), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=8,
                    color=P.text_secondary)
    ax.set_yticks(y, [s.label for s in stats], fontsize=8)
    ax.set_xlabel("hectáreas")
    ax.set_title(f"Superficie por ambiente ({value_label.lower()} en la tabla)")
    ax.grid(axis="x", visible=True)
    ax.grid(axis="y", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, max(s.area_ha for s in stats) * 1.28)
    return _finish(fig)


def distribution(values: np.ndarray, index_key: str, reference: float | None = None,
                 size=(6.4, 2.6)) -> bytes:
    idx = get_index(index_key)
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    fig, ax = plt.subplots(figsize=size)
    ax.hist(v, bins=44, color=P.s(0), edgecolor="white", linewidth=0.4)
    if reference is not None:
        ax.axvline(reference, color=P.text_secondary, linewidth=1.4, linestyle="--")
        ax.annotate("media del lote", (reference, ax.get_ylim()[1]), xytext=(4, -10),
                    textcoords="offset points", fontsize=7.5, color=P.text_secondary)
    ax.set_title(f"Distribución interna del {idx.label}")
    ax.set_xlabel(idx.label)
    ax.set_ylabel("píxeles")
    _clean(ax, x_dates=False)
    return _finish(fig)


# --------------------------------------------------------------------------
# Clima
# --------------------------------------------------------------------------
def rain_panel(wx: pd.DataFrame, size=(9.2, 4.2)) -> bytes:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=size, sharex=True,
                                   gridspec_kw={"height_ratios": [1, 1], "hspace": 0.32})
    obs = wx[wx["source"] == "observado"] if "source" in wx else wx
    fc = wx[wx["source"] == "pronóstico"] if "source" in wx else wx.iloc[0:0]

    ax1.bar(_dates(obs["date"]), obs["precip_mm"], color=P.s(0), width=1.0,
            label="Lluvia observada")
    if not fc.empty:
        ax1.bar(_dates(fc["date"]), fc["precip_mm"], color=P.s(0), alpha=0.45, width=1.0,
                edgecolor=P.s(0), linewidth=0.6, label="Pronóstico")
    ax1.set_title("Lluvia diaria (mm)")
    ax1.set_ylabel("mm/día")
    _clean(ax1)
    if not fc.empty:
        ax1.legend(loc="upper left", ncol=2)

    acc = pd.to_numeric(wx["precip_mm"], errors="coerce").fillna(0).cumsum()
    ax2.plot(_dates(wx["date"]), acc, color=P.s(0), linewidth=2, label="Acumulado")
    if "precip_norm" in wx.columns and wx["precip_norm"].notna().any():
        norm = pd.to_numeric(wx["precip_norm"], errors="coerce").fillna(0).cumsum()
        ax2.plot(_dates(wx["date"]), norm, color=P.muted, linewidth=1.6, linestyle="--",
                 label="Normal 1991–2020")
        ax2.legend(loc="upper left", ncol=2)
    ax2.set_title("Lluvia acumulada (mm)")
    ax2.set_ylabel("mm")
    _clean(ax2)
    return _finish(fig)


def water_balance_panel(wb: pd.DataFrame, size=(9.2, 4.2)) -> bytes:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=size, sharex=True,
                                   gridspec_kw={"height_ratios": [1.5, 1], "hspace": 0.34})
    x = _dates(wb["date"])
    ax1.fill_between(x, 0, wb["agua_util_pct"], color=P.s(0), alpha=0.18, linewidth=0)
    ax1.plot(x, wb["agua_util_pct"], color=P.s(0), linewidth=2)
    for level, label, color in ((50, "umbral de alerta", P.warning),
                                (25, "estrés severo", P.critical)):
        ax1.axhline(level, color=color, linewidth=1, linestyle=":")
        ax1.annotate(label, (x[-1], level), xytext=(-4, 4), textcoords="offset points",
                     ha="right", fontsize=7.5, color=P.text_secondary)
    ax1.set_ylim(0, 105)
    ax1.set_title("Agua útil almacenada (% de la capacidad)")
    ax1.set_ylabel("%")
    _clean(ax1)

    ax2.plot(x, wb["ks"], color=P.s(1), linewidth=2)
    ax2.set_ylim(0, 1.05)
    ax2.set_title("Coeficiente de estrés hídrico (1 = sin estrés)")
    ax2.set_ylabel("Ks")
    _clean(ax2)
    return _finish(fig)


def gdd_chart(gdd: pd.DataFrame, crop, size=(9.2, 3.0)) -> bytes:
    fig, ax = plt.subplots(figsize=size)
    ax.plot(_dates(gdd["date"]), gdd["gdd_acum"], color=P.s(3), linewidth=2)
    techo = float(gdd["gdd_acum"].max() or 0) * 1.05
    for st in crop.stages:
        level = st.gdd_frac * crop.gdd_cycle
        if 0 < level <= techo:
            ax.axhline(level, color=P.grid, linewidth=0.9)
            ax.annotate(st.name, (0.005, level), xycoords=("axes fraction", "data"),
                        xytext=(0, 3), textcoords="offset points", fontsize=7.5,
                        color=P.muted)
    ax.set_title(f"Suma térmica acumulada — {crop.label}")
    ax.set_ylabel("°C día")
    _clean(ax)
    return _finish(fig)


def temperature_panel(wx: pd.DataFrame, crop, size=(9.2, 3.0)) -> bytes:
    fig, ax = plt.subplots(figsize=size)
    x = _dates(wx["date"])
    ax.fill_between(x, wx["tmin"], wx["tmax"], color=P.s(1), alpha=0.16, linewidth=0,
                    label="Rango diario")
    ax.plot(x, wx["tmax"], color=P.s(1), linewidth=1.6, label="Máxima")
    ax.plot(x, wx["tmin"], color=P.s(0), linewidth=1.6, label="Mínima")
    ax.axhline(crop.heat_critical_c, color=P.critical, linewidth=1, linestyle=":")
    ax.axhline(crop.frost_critical_c, color=P.s(0), linewidth=1, linestyle=":")
    ax.set_title("Temperaturas y umbrales de daño", pad=26)
    ax.set_ylabel("°C")
    _clean(ax)
    _legend_above(ax, ncol=3)
    return _finish(fig)


def rgb_thumbnail(rgb: np.ndarray, title: str = "Color natural", size=(4.4, 4.4)) -> bytes:
    fig, ax = plt.subplots(figsize=size)
    ax.imshow(np.clip(rgb, 0, 1))
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    return _finish(fig)
