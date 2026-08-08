"""Motor satelital: Sentinel-2 sobre Google Earth Engine.

Responsabilidades:
  * inicializar y diagnosticar la conexión,
  * armar la colección enmascarada por nubes y sombras,
  * calcular cualquier índice del catálogo del lado del servidor,
  * devolver series temporales estadísticas y rásteres recortados al lote.

Todo lo caro pasa por el caché en disco: reabrir un lote es instantáneo.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from ..cache import disk_cache
from ..config import SETTINGS
from ..geo import area_ha, to_geojson, to_shape, utm_epsg
from ..indices import BAND_MAP, EEImage, EEOps, get_index
from ..models import SceneInfo, uniformity_score

log = logging.getLogger(__name__)

_INITIALIZED = False
_INIT_ERROR: str | None = None

# Clases de la banda SCL de Sentinel-2 que descartamos
SCL_BAD = [1, 3, 8, 9, 10, 11]  # saturado, sombra de nube, nube media/alta, cirrus, nieve
SCL_LABELS = {
    0: "Sin dato", 1: "Saturado", 2: "Suelo oscuro", 3: "Sombra de nube", 4: "Vegetación",
    5: "Suelo desnudo", 6: "Agua", 7: "Sin clasificar", 8: "Nube media", 9: "Nube alta",
    10: "Cirrus", 11: "Nieve",
}


class SatelliteError(RuntimeError):
    """Error del motor satelital, con mensaje presentable al usuario."""


# --------------------------------------------------------------------------
# Inicialización
# --------------------------------------------------------------------------
_AUTH_SOURCE: str | None = None

EE_SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
]


def _init_service_account(ee, proj: str) -> tuple[str, str]:
    """Cuenta de servicio pasada por secreto: el camino para cualquier servidor."""
    info = json.loads(SETTINGS.ee_service_account_json)
    creds = ee.ServiceAccountCredentials(
        info["client_email"], key_data=SETTINGS.ee_service_account_json
    )
    ee.Initialize(creds, project=proj or info.get("project_id"))
    return "cuenta de servicio", info.get("client_email", "")


def _init_adc(ee, proj: str) -> tuple[str, str]:
    """Credenciales por defecto del entorno (Cloud Run, GCE, GKE).

    Es la vía recomendada en Google Cloud: no hay archivo de clave que rotar
    ni que filtrar, la identidad la pone el propio servicio.
    """
    import google.auth

    creds, detected = google.auth.default(scopes=EE_SCOPES)
    ee.Initialize(creds, project=proj or detected)
    return "credenciales del entorno (ADC)", str(detected or proj)


def _init_local(ee, proj: str) -> tuple[str, str]:
    """Credencial de usuario guardada por `earthengine authenticate`."""
    ee.Initialize(project=proj) if proj else ee.Initialize()
    return "credencial local de usuario", proj


def init(project: str | None = None, force: bool = False) -> bool:
    """Inicializa Earth Engine probando las credenciales en orden de prioridad.

    1. Cuenta de servicio en `EE_SERVICE_ACCOUNT_JSON` (servidores).
    2. Credencial local de `earthengine authenticate` (desarrollo).
    3. Credenciales por defecto del entorno (Google Cloud).

    Devuelve True si quedó operativo; si no, levanta `SatelliteError` con
    instrucciones concretas según dónde esté corriendo.
    """
    global _INITIALIZED, _INIT_ERROR, _AUTH_SOURCE
    if _INITIALIZED and not force:
        return True
    try:
        import ee
    except ImportError as exc:
        _INIT_ERROR = "Falta la librería earthengine-api (pip install earthengine-api)."
        raise SatelliteError(_INIT_ERROR) from exc

    proj = project or SETTINGS.ee_project
    estrategias = []
    if SETTINGS.ee_service_account_json:
        estrategias.append(_init_service_account)
    estrategias += [_init_local, _init_adc]

    fallos: list[str] = []
    for estrategia in estrategias:
        try:
            fuente, detalle = estrategia(ee, proj)
            ee.Number(1).getInfo()  # ping real: Initialize es perezoso
            _INITIALIZED, _INIT_ERROR = True, None
            _AUTH_SOURCE = f"{fuente}{f' · {detalle}' if detalle else ''}"
            log.info("Earth Engine conectado con %s", _AUTH_SOURCE)
            return True
        except Exception as exc:
            fallos.append(f"{estrategia.__name__.replace('_init_', '')}: {exc}")

    _INITIALIZED = False
    _INIT_ERROR = " | ".join(fallos)
    raise SatelliteError(
        "No se pudo conectar con Google Earth Engine.\n\n"
        "Se probaron estas credenciales, en orden:\n  - " + "\n  - ".join(fallos) + "\n\n"
        "En tu máquina: ejecutá `earthengine authenticate` y definí EE_PROJECT.\n"
        "En un servidor: cargá el JSON de una cuenta de servicio registrada en Earth "
        "Engine en el secreto EE_SERVICE_ACCOUNT_JSON.\n"
        "En Google Cloud: alcanza con asignarle al servicio una cuenta con permiso "
        "de Earth Engine; no hace falta archivo de clave."
    )


def status() -> dict[str, Any]:
    return {
        "inicializado": _INITIALIZED,
        "proyecto": SETTINGS.ee_project,
        "credencial": _AUTH_SOURCE,
        "error": _INIT_ERROR,
    }


def _ee():
    init()
    import ee

    return ee


def ee_geometry(geometry: dict | Any):
    ee = _ee()
    gj = to_geojson(geometry)
    return ee.Geometry(gj, proj="EPSG:4326", geodesic=False)


# --------------------------------------------------------------------------
# Colección enmascarada
# --------------------------------------------------------------------------
def _add_cloud_bands(img):
    ee = _ee()
    prob = ee.Image(img.get("s2cloudless")).select("probability").rename("cloud_prob")
    return img.addBands(prob)


def masked_collection(geometry, start: date, end: date, max_cloud: float | None = None):
    """Colección Sentinel-2 SR con nubes, sombras y cirrus enmascarados.

    Combina la probabilidad de s2cloudless con la clasificación SCL y dilata
    el resultado para atrapar los bordes de nube, que son los que más ensucian
    los promedios de un lote.
    """
    ee = _ee()
    aoi = ee_geometry(geometry)
    max_cloud = SETTINGS.max_scene_cloud_pct if max_cloud is None else max_cloud
    s_str, e_str = str(start), str(end + timedelta(days=1))

    s2 = (
        ee.ImageCollection(SETTINGS.s2_collection)
        .filterBounds(aoi)
        .filterDate(s_str, e_str)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
    )
    clouds = (
        ee.ImageCollection(SETTINGS.s2_cloud_prob_collection)
        .filterBounds(aoi)
        .filterDate(s_str, e_str)
    )
    joined = ee.ImageCollection(
        ee.Join.saveFirst("s2cloudless").apply(
            primary=s2,
            secondary=clouds,
            condition=ee.Filter.equals(leftField="system:index", rightField="system:index"),
        )
    )

    thr = SETTINGS.cloud_prob_threshold
    buf = SETTINGS.cloud_buffer_m

    def _mask(img):
        img = _add_cloud_bands(img)
        scl = img.select("SCL")
        bad = img.select("cloud_prob").gt(thr)
        for cls in SCL_BAD:
            bad = bad.Or(scl.eq(cls))
        # dilatación: los bordes de nube son el principal sesgo en lotes chicos
        bad = bad.focal_max(radius=buf, units="meters")
        optical = img.select("B.*").divide(10_000)
        clean = optical.updateMask(bad.Not()).addBands(scl)
        return ee.Image(
            clean.copyProperties(
                img,
                ["system:time_start", "system:index", "CLOUDY_PIXEL_PERCENTAGE", "SPACECRAFT_NAME"],
            )
        ).set("system:time_start", img.get("system:time_start"))

    return joined.map(_mask).sort("system:time_start")


def with_index(img, index_key: str):
    """Agrega al `img` una banda `idx` con el índice pedido."""
    idx = get_index(index_key)
    bands = {role: EEImage(img.select(BAND_MAP[role])) for role in idx.bands}
    result = idx.compute(bands, EEOps())
    band = result.img if isinstance(result, EEImage) else result
    return img.addBands(band.rename("idx").toFloat())


# --------------------------------------------------------------------------
# Serie temporal
# --------------------------------------------------------------------------
def _reducer():
    ee = _ee()
    return (
        ee.Reducer.mean()
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.percentile([10, 50, 90]), sharedInputs=True)
        .combine(ee.Reducer.minMax(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )


def _chunks(start: date, end: date, months: int = 12) -> list[tuple[date, date]]:
    out, cur = [], start
    while cur < end:
        nxt = min(end, cur + timedelta(days=31 * months))
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


@disk_cache("s2series", ttl_hours=12)
def index_series(
    geometry: dict,
    start: date,
    end: date,
    index_key: str = "NDVI",
    max_cloud: float | None = None,
    min_valid_fraction: float | None = None,
    scale: int | None = None,
) -> pd.DataFrame:
    """Serie temporal de estadísticos del índice sobre el lote.

    Devuelve una fila por fecha con observación válida: media, mediana,
    percentiles 10/90, desvío, mínimo, máximo, cobertura de píxeles válidos y
    nubosidad de la escena.
    """
    ee = _ee()
    scale = scale or SETTINGS.s2_scale_m
    min_valid = SETTINGS.min_valid_fraction if min_valid_fraction is None else min_valid_fraction
    aoi = ee_geometry(geometry)
    total_px = max(1.0, area_ha(geometry) * 10_000.0 / (scale**2))
    reducer = _reducer()

    rows: list[dict] = []
    for c_start, c_end in _chunks(start, end):
        col = masked_collection(geometry, c_start, c_end, max_cloud).map(
            lambda im: with_index(im, index_key)
        )

        def to_feature(img):
            stats = img.select("idx").reduceRegion(
                reducer=reducer, geometry=aoi, scale=scale, maxPixels=1e9, bestEffort=True
            )
            return ee.Feature(None, stats).set({
                "date": img.date().format("YYYY-MM-dd"),
                "scene_id": img.get("system:index"),
                "cloud_scene": img.get("CLOUDY_PIXEL_PERCENTAGE"),
            })

        try:
            feats = ee.FeatureCollection(col.map(to_feature)).getInfo()["features"]
        except Exception as exc:  # pragma: no cover - depende del servicio
            raise SatelliteError(
                f"Earth Engine rechazó la consulta entre {c_start} y {c_end}: {exc}"
            ) from exc

        for f in feats:
            p = f["properties"]
            if p.get("idx_mean") is None:
                continue
            rows.append({
                "date": pd.to_datetime(p["date"]).date(),
                "scene_id": p.get("scene_id", ""),
                "cloud_scene_pct": float(p.get("cloud_scene") or 0.0),
                "valid_fraction": min(1.0, float(p.get("idx_count") or 0) / total_px),
                "mean": float(p["idx_mean"]),
                "median": float(p.get("idx_p50") or p["idx_mean"]),
                "p10": float(p.get("idx_p10") or np.nan),
                "p90": float(p.get("idx_p90") or np.nan),
                "std": float(p.get("idx_stdDev") or 0.0),
                "min": float(p.get("idx_min") or np.nan),
                "max": float(p.get("idx_max") or np.nan),
            })

    if not rows:
        return pd.DataFrame(columns=[
            "date", "scene_id", "cloud_scene_pct", "valid_fraction", "mean", "median",
            "p10", "p90", "std", "min", "max", "cv", "uniformity",
        ])

    df = pd.DataFrame(rows).sort_values("date")
    # Una misma fecha puede traer dos escenas (órbitas contiguas): nos quedamos
    # con la de mayor cobertura válida.
    df = df.sort_values(["date", "valid_fraction"]).groupby("date", as_index=False).last()
    df = df[df["valid_fraction"] >= min_valid].reset_index(drop=True)
    df["cv"] = (df["std"] / df["mean"].replace(0, np.nan)).fillna(0.0)
    df["uniformity"] = [uniformity_score(m, s) for m, s in zip(df["mean"], df["std"])]
    df["index"] = index_key
    return df


def scenes_from_df(df: pd.DataFrame) -> list[SceneInfo]:
    return [
        SceneInfo(
            date=r["date"], scene_id=str(r["scene_id"]), cloud_scene_pct=float(r["cloud_scene_pct"]),
            valid_fraction=float(r["valid_fraction"]), mean=float(r["mean"]),
            median=float(r["median"]), p10=float(r["p10"]), p90=float(r["p90"]),
            std=float(r["std"]), min=float(r["min"]), max=float(r["max"]),
        )
        for _, r in df.iterrows()
    ]


# --------------------------------------------------------------------------
# Imágenes puntuales y compuestos
# --------------------------------------------------------------------------
def image_for_date(geometry, target: date, index_key: str = "NDVI", window_days: int = 10):
    """Mejor imagen disponible cerca de una fecha (la de menor nubosidad)."""
    ee = _ee()
    start, end = target - timedelta(days=window_days), target + timedelta(days=window_days)
    col = masked_collection(geometry, start, end).map(lambda im: with_index(im, index_key))
    n = col.size().getInfo()
    if not n:
        raise SatelliteError(
            f"No hay imágenes Sentinel-2 utilizables entre {start} y {end}. "
            "Ampliá la ventana o relajá el filtro de nubes."
        )
    img = ee.Image(col.sort("CLOUDY_PIXEL_PERCENTAGE").first())
    return img.clip(ee_geometry(geometry))


def composite(geometry, start: date, end: date, index_key: str = "NDVI", reducer: str = "median"):
    """Compuesto temporal del índice (mediana por defecto: robusto a residuos de nube)."""
    ee = _ee()
    col = masked_collection(geometry, start, end).map(lambda im: with_index(im, index_key))
    red = {"median": col.median, "mean": col.mean, "max": col.max, "min": col.min}[reducer]
    return ee.Image(red()).clip(ee_geometry(geometry))


def tile_url(image, vmin: float, vmax: float, palette: list[str] | tuple[str, ...],
             band: str = "idx") -> str:
    """URL de teselas XYZ para pintar la imagen en el mapa."""
    ee = _ee()
    vis = {"min": vmin, "max": vmax, "palette": list(palette), "bands": [band]}
    return ee.Image(image).getMapId(vis)["tile_fetcher"].url_format


def rgb_tile_url(geometry, target: date, window_days: int = 10, gamma: float = 1.15) -> str:
    """Color natural de la fecha más cercana, para ver el lote como se ve desde arriba."""
    ee = _ee()
    start, end = target - timedelta(days=window_days), target + timedelta(days=window_days)
    col = masked_collection(geometry, start, end)
    if not col.size().getInfo():
        raise SatelliteError("No hay imagen de color natural disponible en esa ventana.")
    img = ee.Image(col.sort("CLOUDY_PIXEL_PERCENTAGE").first())
    vis = {"bands": ["B4", "B3", "B2"], "min": 0.02, "max": 0.30, "gamma": gamma}
    return img.getMapId(vis)["tile_fetcher"].url_format


# --------------------------------------------------------------------------
# Descarga de rásteres
# --------------------------------------------------------------------------
@disk_cache("s2raster", ttl_hours=72)
def download_index_raster(
    geometry: dict,
    start: date,
    end: date,
    index_key: str = "NDVI",
    scale: int | None = None,
    mode: str = "composite",
) -> dict[str, Any]:
    """Descarga el índice recortado al lote como array de numpy.

    `mode` = "composite" (mediana del período) o "single" (mejor escena cerca
    de `end`). Devuelve valores, máscara de validez, transform y CRS métrico.
    """
    ee = _ee()
    scale = scale or SETTINGS.s2_scale_m
    lat, lon = to_shape(geometry).centroid.y, to_shape(geometry).centroid.x
    crs = f"EPSG:{utm_epsg(lon, lat)}"

    # Guardia de tamaño: subimos la resolución de salida antes que reventar la descarga
    px = area_ha(geometry) * 10_000 / (scale**2)
    while px > SETTINGS.max_download_px and scale < 160:
        scale *= 2
        px /= 4

    img = (
        composite(geometry, start, end, index_key)
        if mode == "composite"
        else image_for_date(geometry, end, index_key)
    )
    value = img.select("idx").rename("value")
    valid = value.mask().rename("valid")
    out = value.unmask(-9999).addBands(valid).clip(ee_geometry(geometry))

    url = out.getDownloadURL({
        "region": ee_geometry(geometry),
        "scale": scale,
        "crs": crs,
        "format": "GEO_TIFF",
        "bands": ["value", "valid"],
    })
    return _fetch_geotiff(url, index_key, scale, crs)


def _fetch_geotiff(url: str, index_key: str, scale: int, crs: str) -> dict[str, Any]:
    import rasterio
    import requests

    resp = requests.get(url, timeout=SETTINGS.request_timeout_s)
    if resp.status_code != 200:
        raise SatelliteError(f"Earth Engine no entregó el ráster (HTTP {resp.status_code}).")
    with rasterio.io.MemoryFile(io.BytesIO(resp.content)) as mem, mem.open() as ds:
        value = ds.read(1).astype("float32")
        valid = ds.read(2).astype("float32") > 0
        transform, out_crs = ds.transform, ds.crs
    value[~valid] = np.nan
    value[value <= -9000] = np.nan
    return {
        "values": value,
        "valid": valid,
        "transform": transform,
        "crs": str(out_crs or crs),
        "scale": scale,
        "index": index_key,
    }


@disk_cache("s2rgb", ttl_hours=72)
def download_rgb(geometry: dict, target: date, window_days: int = 12,
                 scale: int | None = None) -> np.ndarray | None:
    """Recorte en color natural del lote (para el PDF y la vista de contexto)."""
    ee = _ee()
    scale = scale or SETTINGS.s2_scale_m
    lat, lon = to_shape(geometry).centroid.y, to_shape(geometry).centroid.x
    crs = f"EPSG:{utm_epsg(lon, lat)}"
    start = target - timedelta(days=window_days)
    col = masked_collection(geometry, start, target + timedelta(days=window_days))
    if not col.size().getInfo():
        return None
    img = ee.Image(col.sort("CLOUDY_PIXEL_PERCENTAGE").first())
    rgb = img.select(["B4", "B3", "B2"]).unmask(0).clip(ee_geometry(geometry))
    url = rgb.getDownloadURL({
        "region": ee_geometry(geometry), "scale": scale, "crs": crs, "format": "GEO_TIFF",
    })
    import rasterio
    import requests

    resp = requests.get(url, timeout=SETTINGS.request_timeout_s)
    if resp.status_code != 200:
        return None
    with rasterio.io.MemoryFile(io.BytesIO(resp.content)) as mem, mem.open() as ds:
        arr = ds.read().astype("float32")
    arr = np.clip((arr - 0.02) / 0.28, 0, 1) ** (1 / 1.15)
    return np.transpose(arr, (1, 2, 0))


@disk_cache("s2scl", ttl_hours=72)
def scene_quality(geometry: dict, start: date, end: date) -> pd.DataFrame:
    """Cuántas observaciones válidas hubo por mes: la 'salud' del monitoreo."""
    df = index_series(geometry, start, end, "NDVI")
    if df.empty:
        return pd.DataFrame(columns=["mes", "observaciones", "cobertura_media"])
    d = df.copy()
    d["mes"] = pd.to_datetime(d["date"]).values.astype("datetime64[M]")
    g = d.groupby("mes").agg(observaciones=("date", "count"),
                             cobertura_media=("valid_fraction", "mean")).reset_index()
    return g
