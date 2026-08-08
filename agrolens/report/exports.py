"""Exportaciones.

El análisis no termina en la pantalla: los datos salen en formatos que abren
el resto de las herramientas del productor (Excel, QGIS, el monitor de la
sembradora).
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ..models import SEVERITY_LABEL


# --------------------------------------------------------------------------
# Tabulares
# --------------------------------------------------------------------------
def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")


def excel_workbook(res: Any) -> bytes:
    """Libro de Excel con todas las tablas del análisis, una por hoja."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", datetime_format="dd/mm/yyyy",
                        date_format="dd/mm/yyyy") as xw:
        _sheet(xw, "Resumen", _summary_frame(res))
        if not res.series.empty:
            _sheet(xw, "Serie satelital", res.series)
        if not res.curve.empty:
            _sheet(xw, "Curva diaria", res.curve)
        if not res.clima.empty:
            _sheet(xw, "Clima diario", res.clima)
        if not res.balance.empty:
            cols = [c for c in ("date", "precip_mm", "et0_mm", "kc", "etc_mm", "eta_mm", "ks",
                                "agua_util_mm", "agua_util_pct", "deficit_acum_mm", "gdd",
                                "gdd_acum", "etapa") if c in res.balance.columns]
            _sheet(xw, "Balance hídrico", res.balance[cols])
        if res.zonas:
            _sheet(xw, "Ambientes", pd.DataFrame([{
                "Zona": s.zone + 1, "Ambiente": s.label, "Superficie (ha)": s.area_ha,
                "% del lote": s.pct, "Índice medio": round(s.mean, 3),
                "Desvío": round(s.std, 3),
            } for s in res.zonas["stats"]]))
        if not res.ranking.empty:
            _sheet(xw, "Comparación histórica", res.ranking)
        if res.alertas:
            _sheet(xw, "Hallazgos", pd.DataFrame([{
                "Severidad": SEVERITY_LABEL.get(a.severity, a.severity), "Origen": a.source,
                "Hallazgo": a.title, "Detalle": a.detail, "Recomendación": a.recommendation,
            } for a in res.alertas]))
        _sheet(xw, "Metodología", _methodology_frame(res))
    return buf.getvalue()


def _sheet(xw: pd.ExcelWriter, name: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    clean = df.copy()
    for c in clean.columns:  # Excel no acepta tz ni objetos raros
        if pd.api.types.is_datetime64_any_dtype(clean[c]):
            clean[c] = clean[c].dt.tz_localize(None) if getattr(clean[c].dt, "tz", None) else clean[c]
    clean.to_excel(xw, sheet_name=name[:31], index=False)
    ws = xw.sheets[name[:31]]
    header = xw.book.add_format({"bold": True, "bg_color": "#e1e0d9", "border": 0})
    for i, col in enumerate(clean.columns):
        width = max(11, min(42, int(clean[col].astype(str).str.len().max() or 10) + 2, 42))
        ws.set_column(i, i, max(width, len(str(col)) + 2))
        ws.write(0, i, str(col), header)
    ws.freeze_panes(1, 0)


def _summary_frame(res: Any) -> pd.DataFrame:
    score, label = res.salud()
    rows = [
        ("Lote", res.lote.name),
        ("Establecimiento", res.lote.farm),
        ("Cultivo", res.crop.label),
        ("Superficie (ha)", round(res.lote.area_ha, 2)),
        ("Fecha de siembra", res.lote.sowing_date),
        ("Período analizado", f"{res.config.start:%d/%m/%Y} a {res.config.end:%d/%m/%Y}"),
        ("Índice principal", res.config.index),
        ("Estado general", f"{score}/100 · {label}"),
        ("Valor actual del índice", res.ultimo_valor),
        ("Última imagen", res.ultima_fecha),
        ("Observaciones válidas", len(res.series)),
    ]
    if res.estres:
        rows += [
            ("Agua útil actual (%)", round(res.estres.get("agua_util_actual_pct", 0), 1)),
            ("Días con estrés hídrico", res.estres.get("dias_estres")),
            ("Satisfacción hídrica del ciclo", round(res.estres.get("satisfaccion_hidrica", 0), 2)),
        ]
    if res.resumen_clima:
        rows += [
            ("Lluvia acumulada (mm)", round(res.resumen_clima.get("lluvia_total_mm", 0), 1)),
            ("ET0 acumulada (mm)", round(res.resumen_clima.get("et0_total_mm", 0), 1)),
        ]
    if res.rendimiento.get("estimado_tha"):
        r = res.rendimiento
        rows.append(("Rendimiento estimado (t/ha)",
                     f"{r['estimado_tha']} (rango {r['rango_tha'][0]}–{r['rango_tha'][1]})"))
    if res.resumen_historia:
        rows.append(("Percentil histórico", res.resumen_historia.get("percentil_actual")))
    return pd.DataFrame(rows, columns=["Concepto", "Valor"])


def _methodology_frame(res: Any) -> pd.DataFrame:
    kc_src = res.balance.attrs.get("kc_source", "—") if not res.balance.empty else "—"
    return pd.DataFrame([
        ("Fuente satelital", "Copernicus Sentinel-2 L2A (armonizado), 10 m"),
        ("Enmascarado de nubes", "s2cloudless + clasificación SCL, con dilatación de 60 m"),
        ("Fuente climática", "Reanálisis ERA5 vía Open-Meteo; pronóstico de 16 días"),
        ("Normales climáticas", "1991–2020 en el mismo punto"),
        ("Suavizado de la curva", f"Savitzky-Golay, ventana de {res.config.smoothing_days} días"),
        ("Coeficiente de cultivo", kc_src),
        ("Balance hídrico", "FAO-56 de un reservorio, con Ks por agotamiento"),
        ("Zonificación", "k-medias sobre el compuesto del índice, con filtro de mediana"),
        ("Modo demostración", "Sí — datos sintéticos" if res.modo_demo else "No"),
        ("Generado", date.today().strftime("%d/%m/%Y")),
    ], columns=["Aspecto", "Detalle"])


# --------------------------------------------------------------------------
# Geoespaciales
# --------------------------------------------------------------------------
def geojson_bytes(obj: Any) -> bytes:
    """Serializa un GeoDataFrame, un dict GeoJSON o una geometría."""
    if hasattr(obj, "__geo_interface__"):
        data = obj.__geo_interface__
    elif isinstance(obj, dict):
        data = obj
    else:
        from ..geo import to_geojson

        data = to_geojson(obj)
    return json.dumps(data, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def shapefile_zip(gdf, layer_name: str = "ambientes") -> bytes:
    """Shapefile comprimido: el formato que todavía piden la mayoría de los monitores."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / f"{layer_name}.shp"
        out = gdf.copy()
        out.columns = [c[:10] for c in out.columns]  # límite de nombres del formato
        out.to_file(base, driver="ESRI Shapefile", encoding="utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmp).iterdir():
                zf.write(f, f.name)
        return buf.getvalue()


def geotiff_bytes(raster: dict, nodata: float = -9999.0) -> bytes:
    """Ráster del índice como GeoTIFF, listo para QGIS."""
    import rasterio

    values = np.asarray(raster["values"], dtype="float32")
    data = np.where(np.isfinite(values), values, nodata)
    profile = {
        "driver": "GTiff", "height": data.shape[0], "width": data.shape[1], "count": 1,
        "dtype": "float32", "crs": raster["crs"], "transform": raster["transform"],
        "nodata": nodata, "compress": "deflate", "tiled": True,
    }
    buf = io.BytesIO()
    with rasterio.io.MemoryFile() as mem:
        with mem.open(**profile) as ds:
            ds.write(data, 1)
            ds.set_band_description(1, raster.get("index", "indice"))
        buf.write(mem.read())
    return buf.getvalue()


def zones_package(res: Any, prescription: pd.DataFrame | None = None) -> bytes:
    """Paquete completo de ambientes: GeoJSON + shapefile + tabla + GeoTIFF."""
    from ..analytics.zones import zone_polygons

    if not res.zonas:
        raise ValueError("No hay zonificación calculada para exportar.")

    gdf = zone_polygons(res.zonas)
    if prescription is not None and not prescription.empty:
        dose_col = [c for c in prescription.columns if c.startswith("dosis_")][0]
        gdf["dosis"] = gdf["zona"].map(dict(zip(prescription["zona"] - 1, prescription[dose_col])))

    buf = io.BytesIO()
    slug = _slug(res.lote.name)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}_ambientes.geojson", geojson_bytes(gdf))
        zf.writestr(f"{slug}_ambientes_shp.zip", shapefile_zip(gdf, f"{slug}_ambientes"))
        zf.writestr(f"{slug}_lote.geojson", geojson_bytes(res.lote.geometry))
        tabla = pd.DataFrame([{
            "zona": s.zone + 1, "ambiente": s.label, "superficie_ha": s.area_ha,
            "porcentaje": s.pct, "indice_medio": round(s.mean, 3),
        } for s in res.zonas["stats"]])
        zf.writestr(f"{slug}_ambientes.csv", csv_bytes(tabla))
        if prescription is not None and not prescription.empty:
            zf.writestr(f"{slug}_prescripcion.csv", csv_bytes(prescription))
        if res.raster:
            zf.writestr(f"{slug}_{res.config.index}.tif", geotiff_bytes(res.raster))
        zf.writestr("LEEME.txt", _readme(res).encode("utf-8"))
    return buf.getvalue()


def _readme(res: Any) -> str:
    return (
        f"AgroLens — paquete de ambientes\n"
        f"Lote: {res.lote.name} ({res.lote.farm})\n"
        f"Cultivo: {res.crop.label}\n"
        f"Índice: {res.config.index}\n"
        f"Período: {res.config.start:%d/%m/%Y} a {res.config.end:%d/%m/%Y}\n"
        f"Generado: {date.today():%d/%m/%Y}\n\n"
        "Contenido:\n"
        "  *_ambientes.geojson     zonas en EPSG:4326\n"
        "  *_ambientes_shp.zip     las mismas zonas en shapefile\n"
        "  *_lote.geojson          perímetro del lote\n"
        "  *_ambientes.csv         superficie e índice medio por ambiente\n"
        "  *_prescripcion.csv      dosis por ambiente (si se generó)\n"
        "  *.tif                   ráster del índice, en la proyección UTM local\n\n"
        "Las zonas se numeran de menor a mayor índice: la zona 1 es siempre la de menor vigor.\n"
        + ("\nATENCIÓN: generado en modo demostración con datos sintéticos.\n" if res.modo_demo else "")
    )


def _slug(text: str) -> str:
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", t).strip("_").lower() or "lote"
