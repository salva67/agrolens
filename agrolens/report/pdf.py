"""Informe PDF.

El informe se arma desde el mismo `AnalysisResult` que dibuja la pantalla, así
que no puede contradecirla. Está pensado para imprimirse y llevarse al campo:
hallazgos primero, evidencia después, metodología al final.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from ..config import APP_NAME, APP_VERSION, LIGHT
from ..models import SEVERITY_LABEL
from . import figures

log = logging.getLogger(__name__)

PAGE_W = 21.0  # cm
INK = LIGHT.text_primary
INK_SOFT = LIGHT.text_secondary
MUTED = LIGHT.muted
RULE = LIGHT.grid
BRAND = "#0d5c1c"

SEVERITY_HEX = {
    "critical": LIGHT.critical, "serious": LIGHT.serious, "warning": LIGHT.warning,
    "info": LIGHT.s(0), "good": LIGHT.good,
}


class PDFError(RuntimeError):
    """Falta una dependencia o falló el armado del informe."""


def _require():
    try:
        import reportlab  # noqa: F401
    except ImportError as exc:
        raise PDFError(
            "Falta la librería reportlab. Instalala con:  pip install reportlab"
        ) from exc


def _safe(render, *args, **kwargs) -> bytes | None:
    """Dibuja un gráfico; si falla, el informe sigue sin él."""
    try:
        return render(*args, **kwargs)
    except Exception as exc:
        log.warning("No se pudo dibujar el gráfico %s: %s", getattr(render, "__name__", "?"), exc)
        return None


# --------------------------------------------------------------------------
def build(res: Any, *, include_charts: bool = True, logo_path: str | None = None) -> bytes:
    """Genera el informe completo y devuelve los bytes del PDF."""
    _require()
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
        Paragraph, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    S = {
        "h1": ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                             fontSize=20, leading=24, textColor=colors.HexColor(INK),
                             spaceAfter=2, alignment=TA_LEFT),
        "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=colors.HexColor(INK),
                             spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=styles["Heading3"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=colors.HexColor(INK_SOFT),
                             spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                               fontSize=9.5, leading=13.5, textColor=colors.HexColor(INK)),
        "small": ParagraphStyle("small", parent=styles["BodyText"], fontName="Helvetica",
                                fontSize=8, leading=11, textColor=colors.HexColor(MUTED)),
        "kpi_v": ParagraphStyle("kpi_v", fontName="Helvetica-Bold", fontSize=16, leading=18,
                                textColor=colors.HexColor(INK)),
        "kpi_l": ParagraphStyle("kpi_l", fontName="Helvetica", fontSize=7.5, leading=9.5,
                                textColor=colors.HexColor(MUTED)),
    }

    doc = BaseDocTemplate(
        buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
        topMargin=2.4 * cm, bottomMargin=1.7 * cm,
        title=f"Reporte de monitoreo — {res.lote.name}", author=APP_NAME,
        subject=f"{res.crop.label} · {res.config.start} a {res.config.end}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame],
                                       onPage=_page_furniture(res, logo_path))])

    story: list[Any] = []
    story += _cover(res, S, cm)
    story += _kpis(res, S, cm)
    story += _findings(res, S, cm)

    if include_charts:
        story += _evidence(res, S, cm)

    story.append(PageBreak())
    story += _detail_tables(res, S, cm)
    story += _methodology(res, S, cm)

    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Secciones
# --------------------------------------------------------------------------
def _cover(res, S, cm) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    lote = res.lote
    sub = " · ".join(x for x in [lote.farm, res.crop.label, f"{lote.area_ha:.1f} ha"] if x)
    meta = [
        ["Período analizado", f"{res.config.start:%d/%m/%Y} — {res.config.end:%d/%m/%Y}"],
        ["Índice principal", res.config.index],
        ["Siembra", f"{lote.sowing_date:%d/%m/%Y}" if lote.sowing_date else "no informada"],
        ["Última imagen", f"{res.ultima_fecha:%d/%m/%Y}" if res.ultima_fecha else "sin datos"],
        ["Imágenes válidas", f"{len(res.series)}"],
        ["Coordenadas", f"{lote.centroid[0]:.5f}, {lote.centroid[1]:.5f}"],
    ]
    t = Table(meta, colWidths=[4.2 * cm, 12.6 * cm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica", 8.5),
        ("FONT", (1, 0), (1, -1), "Helvetica-Bold", 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor(MUTED)),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor(INK)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor(RULE)),
    ]))

    out = [
        Paragraph("Reporte de monitoreo agrícola", S["h1"]),
        Paragraph(f'<font color="{INK_SOFT}" size="11">{lote.name} — {sub}</font>', S["body"]),
        Spacer(1, 0.45 * cm), t, Spacer(1, 0.3 * cm),
    ]
    if res.modo_demo:
        out.append(_callout(
            "Informe generado en MODO DEMOSTRACIÓN con datos sintéticos. No usar para decisiones.",
            LIGHT.critical, S, cm))
    for aviso in res.avisos[:3]:
        out.append(_callout(aviso, LIGHT.warning, S, cm))
    return out


def _callout(text: str, color: str, S, cm):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    t = Table([[Paragraph(text, S["body"])]], colWidths=[16.8 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(color + "22")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(color)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _kpis(res, S, cm) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    score, label = res.salud()
    cells: list[tuple[str, str, str]] = [
        (f"{score}", f"Estado general · {label}", _score_color(score)),
    ]
    if res.ultimo_valor is not None:
        cells.append((f"{res.ultimo_valor:.2f}", f"{res.config.index} actual", INK))
    if res.trend:
        sl = res.trend.get("slope_week", 0)
        cells.append((f"{sl:+.3f}", "Tendencia semanal",
                      LIGHT.good if sl >= 0 else LIGHT.critical))
    if res.uniformidad is not None:
        cells.append((f"{res.uniformidad:.0f}", "Uniformidad / 100", INK))
    if res.estres:
        aw = res.estres.get("agua_util_actual_pct", 0)
        cells.append((f"{aw:.0f} %", "Agua útil del perfil",
                      LIGHT.critical if aw < 30 else INK))
    if res.resumen_clima:
        cells.append((f"{res.resumen_clima.get('lluvia_total_mm', 0):.0f}", "Lluvia del período (mm)", INK))
    if res.resumen_historia:
        pc = res.resumen_historia.get("percentil_actual", 50)
        cells.append((f"P{pc:.0f}", "Percentil histórico",
                      LIGHT.good if pc >= 65 else LIGHT.critical if pc < 35 else INK))
    if res.rendimiento.get("estimado_tha"):
        cells.append((f"{res.rendimiento['estimado_tha']:.1f}", "Rinde estimado (t/ha)", INK))

    cells = cells[:8]
    n = min(4, len(cells))
    rows = [cells[i:i + n] for i in range(0, len(cells), n)]
    data, styles_extra = [], []
    for r_i, row in enumerate(rows):
        vals, labs = [], []
        for c_i, (v, l, color) in enumerate(row):
            vals.append(Paragraph(f'<font color="{color}">{v}</font>', S["kpi_v"]))
            labs.append(Paragraph(l, S["kpi_l"]))
        while len(vals) < n:
            vals.append("")
            labs.append("")
        data += [vals, labs]
        styles_extra.append(("LINEBELOW", (0, r_i * 2 + 1), (-1, r_i * 2 + 1), 0.4,
                             colors.HexColor(RULE)))

    t = Table(data, colWidths=[16.8 / n * cm] * n, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        *styles_extra,
    ]))
    return [Spacer(1, 0.35 * cm), t, Spacer(1, 0.1 * cm)]


def _score_color(score: int) -> str:
    if score >= 80:
        return LIGHT.good
    if score >= 65:
        return BRAND
    if score >= 45:
        return LIGHT.warning
    return LIGHT.critical


def _findings(res, S, cm) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    out = [Paragraph("Hallazgos y recomendaciones", S["h2"])]
    if not res.alertas:
        out.append(Paragraph("No se detectaron hallazgos relevantes en el período analizado.",
                             S["body"]))
        return out

    for a in res.alertas[:10]:
        color = SEVERITY_HEX.get(a.severity, MUTED)
        head = (f'<b>{a.title}</b>  <font color="{MUTED}" size="7.5">'
                f'{SEVERITY_LABEL.get(a.severity, "").upper()} · {a.source.upper()}</font>')
        block = [[Paragraph(head, S["body"])], [Paragraph(a.detail, S["body"])]]
        if a.recommendation:
            block.append([Paragraph(f'<font color="{INK_SOFT}"><b>Qué hacer:</b> '
                                    f'{a.recommendation}</font>', S["body"])])
        t = Table(block, colWidths=[16.6 * cm])
        t.setStyle(TableStyle([
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(color)),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfbf9")),
        ]))
        out += [KeepTogether([t]), Spacer(1, 0.18 * cm)]
    return out


def _evidence(res, S, cm) -> list:
    from reportlab.platypus import Image, PageBreak, Paragraph, Spacer

    out: list[Any] = [PageBreak(), Paragraph("Evidencia satelital", S["h2"])]

    bloques: list[tuple[str, bytes | None, float]] = []
    if not res.series.empty:
        eventos = []
        if res.lote.sowing_date:
            eventos.append((res.lote.sowing_date, "siembra", MUTED))
        if res.fenologia.pos:
            eventos.append((res.fenologia.pos, "pico", MUTED))
        bloques.append(("Evolución del índice",
                        _safe(figures.index_timeseries, res.series, res.curve,
                              res.config.index, eventos), 16.6))
    if not res.banda.empty and not res.ranking.empty:
        bloques.append(("Comparación con campañas anteriores",
                        _safe(figures.history_envelope, res.banda, res.ranking,
                              res.config.index), 16.6))
    if res.raster is not None:
        bloques.append(("Distribución interna del índice",
                        _safe(figures.raster_map, res.raster, res.config.index), 11.5))
        bloques.append(("Histograma del lote",
                        _safe(figures.distribution, res.raster["values"], res.config.index,
                              res.ultimo_valor), 13.5))
    if res.zonas:
        bloques.append(("Ambientes delimitados",
                        _safe(figures.zone_map, res.zonas), 11.5))
        bloques.append(("Superficie por ambiente",
                        _safe(figures.zone_bars, res.zonas["stats"]), 13.5))

    for title, png, ancho in bloques:
        if png is None:
            out.append(Paragraph(f"[{title}: no se pudo generar la imagen.]", S["small"]))
            continue
        out += [Paragraph(title, S["h3"]), _image(png, cm, ancho), Spacer(1, 0.25 * cm)]

    if not res.clima.empty:
        out += [PageBreak(), Paragraph("Condiciones agroclimáticas", S["h2"])]
        clima: list[tuple[str, bytes | None, float]] = [
            ("Lluvias", _safe(figures.rain_panel, res.clima), 16.6),
        ]
        if not res.balance.empty:
            clima += [
                ("Balance hídrico", _safe(figures.water_balance_panel, res.balance), 16.6),
                ("Suma térmica", _safe(figures.gdd_chart, res.balance, res.crop), 16.6),
            ]
        clima.append(("Temperaturas",
                      _safe(figures.temperature_panel, res.clima, res.crop), 16.6))
        for title, png, ancho in clima:
            if png:
                out += [Paragraph(title, S["h3"]), _image(png, cm, ancho), Spacer(1, 0.25 * cm)]
    return out


def _image(png: bytes, cm, width_cm: float = 16.6):
    from reportlab.platypus import Image

    img = Image(io.BytesIO(png))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_cm * cm
    img.drawHeight = width_cm * ratio * cm
    return img


def _detail_tables(res, S, cm) -> list:
    from reportlab.platypus import Paragraph, Spacer

    out = [Paragraph("Detalle numérico", S["h2"])]

    if res.zonas:
        out.append(Paragraph("Ambientes", S["h3"]))
        rows = [["Ambiente", "Superficie (ha)", "% del lote", f"{res.config.index} medio", "Desvío"]]
        rows += [[s.label, f"{s.area_ha:.1f}", f"{s.pct:.0f} %", f"{s.mean:.3f}", f"{s.std:.3f}"]
                 for s in res.zonas["stats"]]
        out += [_table(rows, cm, [5.6, 3.2, 2.6, 3.0, 2.4]), Spacer(1, 0.3 * cm)]

    if res.fenologia and (res.fenologia.pos or res.fenologia.sos):
        from ..analytics.phenology import describe

        out.append(Paragraph("Fenología observada", S["h3"]))
        rows = [["Métrica", "Valor"]] + [list(r) for r in describe(res.fenologia,
                                                                  res.lote.sowing_date)]
        out += [_table(rows, cm, [8.4, 8.4]), Spacer(1, 0.3 * cm)]

    if res.estres:
        out.append(Paragraph("Balance hídrico del ciclo", S["h3"]))
        e = res.estres
        rows = [
            ["Concepto", "Valor"],
            ["Agua útil actual", f"{e.get('agua_util_actual_pct', 0):.0f} % de la capacidad"],
            ["Días con estrés (Ks < 0,8)", f"{e.get('dias_estres', 0)}"],
            ["Días con estrés severo (Ks < 0,5)", f"{e.get('dias_estres_severo', 0)}"],
            ["Días con estrés en período crítico", f"{e.get('dias_estres_criticos', 0)}"],
            ["Satisfacción hídrica del ciclo", f"{e.get('satisfaccion_hidrica', 0) * 100:.0f} %"],
            ["Satisfacción en período crítico", f"{e.get('satisfaccion_critica', 0) * 100:.0f} %"],
            ["Déficit acumulado", f"{e.get('deficit_total_mm', 0):.0f} mm"],
            ["Drenaje profundo", f"{e.get('drenaje_total_mm', 0):.0f} mm"],
        ]
        out += [_table(rows, cm, [10.0, 6.8]), Spacer(1, 0.3 * cm)]

    if res.rendimiento.get("estimado_tha"):
        r = res.rendimiento
        out.append(Paragraph("Rendimiento orientativo", S["h3"]))
        rows = [
            ["Concepto", "Valor"],
            ["Estimación central", f"{r['estimado_tha']:.2f} t/ha"],
            ["Rango probable", f"{r['rango_tha'][0]:.2f} – {r['rango_tha'][1]:.2f} t/ha"],
            ["Biomasa relativa al lote de referencia", f"{r.get('biomasa_relativa', 0):.2f}"],
            ["Penalidad por déficit hídrico", f"{r.get('penalidad_hidrica', 0) * 100:.0f} %"],
            ["Confianza del modelo", r.get("confianza", "—")],
        ]
        out += [_table(rows, cm, [10.0, 6.8]),
                Paragraph("Estimación estadística a partir de la integral del índice y del balance "
                          "hídrico. No reemplaza un aforo a campo.", S["small"]), Spacer(1, 0.3 * cm)]
    return out


def _methodology(res, S, cm) -> list:
    from reportlab.platypus import Paragraph, Spacer

    from .exports import _methodology_frame

    df = _methodology_frame(res)
    rows = [list(df.columns)] + df.values.tolist()
    return [
        Paragraph("Metodología y fuentes", S["h2"]),
        _table(rows, cm, [5.4, 11.4]),
        Spacer(1, 0.3 * cm),
        Paragraph(
            "Los índices espectrales describen el estado del canopeo; no miden directamente "
            "rendimiento, nutrición ni presencia de plagas. Toda decisión de manejo debería "
            "validarse a campo. Las estimaciones de rendimiento y de agua en el perfil son "
            "modelos simplificados y se publican con su rango de incertidumbre.", S["small"]),
    ]


def _table(rows: list[list[str]], cm, widths: list[float]):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    t = Table(rows, colWidths=[w * cm for w in widths], hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(INK)),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(LIGHT.axis)),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor(RULE)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


# --------------------------------------------------------------------------
def _page_furniture(res, logo_path: str | None):
    """Encabezado y pie de cada página."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    lote = res.lote
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")

    def draw(canvas, doc):
        canvas.saveState()
        w, h = doc.pagesize
        # Encabezado
        canvas.setFillColor(colors.HexColor(BRAND))
        canvas.rect(0, h - 1.15 * cm, w, 1.15 * cm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(1.6 * cm, h - 0.75 * cm, APP_NAME.upper())
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - 1.6 * cm, h - 0.75 * cm,
                               f"{lote.name} · {res.crop.label} · {lote.area_ha:.1f} ha")
        # Pie
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(1.6 * cm, 1.0 * cm,
                          f"{APP_NAME} v{APP_VERSION} · generado el {stamp} · "
                          f"Sentinel-2 + ERA5")
        canvas.drawRightString(w - 1.6 * cm, 1.0 * cm, f"Página {doc.page}")
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.setLineWidth(0.4)
        canvas.line(1.6 * cm, 1.35 * cm, w - 1.6 * cm, 1.35 * cm)
        canvas.restoreState()

    return draw
