"""Utilidades geoespaciales: geometrías, superficies, importación de archivos."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform


class GeoError(ValueError):
    """Error de geometría con mensaje apto para mostrar al usuario."""


# --------------------------------------------------------------------------
# Geometría básica
# --------------------------------------------------------------------------
def to_shape(geometry: dict | BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, BaseGeometry):
        return geometry
    if isinstance(geometry, dict):
        if geometry.get("type") == "Feature":
            return shape(geometry["geometry"])
        if geometry.get("type") == "FeatureCollection":
            feats = geometry.get("features") or []
            if not feats:
                raise GeoError("El archivo no contiene ninguna geometría.")
            from shapely.ops import unary_union

            return unary_union([shape(f["geometry"]) for f in feats])
        return shape(geometry)
    raise GeoError("Formato de geometría no reconocido.")


def to_geojson(geom: BaseGeometry | dict) -> dict:
    return mapping(to_shape(geom))


def clean(geom: BaseGeometry) -> BaseGeometry:
    """Repara autointersecciones y descarta partes espurias."""
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise GeoError("La geometría quedó vacía tras la corrección.")
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            raise GeoError("No se encontró ningún polígono en la geometría.")
        from shapely.ops import unary_union

        geom = unary_union(polys)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise GeoError(f"Se esperaba un polígono y se recibió {geom.geom_type}.")
    return geom


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG del huso UTM que contiene al punto (para medir en metros)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _project_to_utm(geom: BaseGeometry):
    from pyproj import CRS, Transformer

    c = geom.centroid
    epsg = utm_epsg(c.x, c.y)
    tr = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    return shp_transform(tr.transform, geom), epsg


def area_ha(geom: BaseGeometry | dict) -> float:
    """Superficie en hectáreas, medida en la proyección UTM local."""
    g = to_shape(geom)
    proj, _ = _project_to_utm(g)
    return float(proj.area / 10_000.0)


def perimeter_m(geom: BaseGeometry | dict) -> float:
    g = to_shape(geom)
    proj, _ = _project_to_utm(g)
    return float(proj.length)


def compactness(geom: BaseGeometry | dict) -> float:
    """Índice de Polsby-Popper: 1 = círculo perfecto, cerca de 0 = muy alargado."""
    g = to_shape(geom)
    proj, _ = _project_to_utm(g)
    if proj.length == 0:
        return 0.0
    import math

    return float(4 * math.pi * proj.area / (proj.length**2))


def centroid_latlon(geom: BaseGeometry | dict) -> tuple[float, float]:
    c = to_shape(geom).centroid
    return (float(c.y), float(c.x))


def bounds(geom: BaseGeometry | dict) -> tuple[float, float, float, float]:
    return tuple(float(v) for v in to_shape(geom).bounds)  # type: ignore[return-value]


def bounds_latlon(geom: BaseGeometry | dict) -> list[list[float]]:
    """Bounds en el orden que espera folium: [[sur, oeste], [norte, este]]."""
    minx, miny, maxx, maxy = bounds(geom)
    return [[miny, minx], [maxy, maxx]]


def buffer_m(geom: BaseGeometry | dict, meters: float) -> BaseGeometry:
    """Buffer en metros (positivo agranda, negativo achica) vía UTM."""
    from pyproj import CRS, Transformer

    g = to_shape(geom)
    proj, epsg = _project_to_utm(g)
    out = proj.buffer(meters)
    if out.is_empty:
        raise GeoError("El buffer negativo eliminó por completo el lote.")
    back = Transformer.from_crs(CRS.from_epsg(epsg), CRS.from_epsg(4326), always_xy=True)
    return shp_transform(back.transform, out)


def inner_field(geom: BaseGeometry | dict, edge_m: float = 15.0) -> BaseGeometry:
    """Descarta el borde del lote (alambrados, cabeceras, árboles vecinos).

    Si el lote es demasiado chico para el retiro pedido, devuelve la geometría
    original en vez de fallar.
    """
    try:
        return buffer_m(geom, -abs(edge_m))
    except GeoError:
        return to_shape(geom)


def simplify(geom: BaseGeometry | dict, tolerance_m: float = 2.0) -> BaseGeometry:
    from pyproj import CRS, Transformer

    g = to_shape(geom)
    proj, epsg = _project_to_utm(g)
    out = proj.simplify(tolerance_m, preserve_topology=True)
    back = Transformer.from_crs(CRS.from_epsg(epsg), CRS.from_epsg(4326), always_xy=True)
    return shp_transform(back.transform, out)


def n_vertices(geom: BaseGeometry | dict) -> int:
    g = to_shape(geom)
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    total = 0
    for p in polys:
        total += len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
    return total


def validate(geom: BaseGeometry | dict, max_ha: float = 20_000.0, min_ha: float = 0.1) -> tuple[BaseGeometry, list[str]]:
    """Limpia y valida un lote. Devuelve la geometría y las advertencias."""
    g = clean(to_shape(geom))
    warn: list[str] = []
    a = area_ha(g)
    if a < min_ha:
        raise GeoError(f"El lote tiene {a:.2f} ha: demasiado chico para un análisis confiable a 10 m.")
    if a > max_ha:
        raise GeoError(f"El lote tiene {a:,.0f} ha y supera el máximo de {max_ha:,.0f} ha.")
    if a < 2:
        warn.append(f"El lote tiene {a:.1f} ha. Con píxeles de 10 m quedan pocas muestras: leé los promedios con cautela.")
    if compactness(g) < 0.15:
        warn.append("La geometría es muy alargada o irregular: el efecto de borde puede pesar en las estadísticas.")
    if n_vertices(g) > 500:
        warn.append("El polígono tiene muchos vértices; se simplificó para acelerar el procesamiento.")
        g = simplify(g, 3.0)
    return g, warn


# --------------------------------------------------------------------------
# Importación de archivos
# --------------------------------------------------------------------------
SUPPORTED_UPLOADS = ("geojson", "json", "kml", "kmz", "zip", "gpkg", "shp")


def read_uploaded(name: str, data: bytes) -> BaseGeometry:
    """Lee un lote desde GeoJSON, KML, KMZ, GPKG o shapefile comprimido."""
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix in ("geojson", "json"):
        return clean(to_shape(json.loads(data.decode("utf-8"))))
    if suffix == "kmz":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise GeoError("El KMZ no contiene ningún archivo KML.")
            data = zf.read(kml_names[0])
            suffix = "kml"
    if suffix in ("kml", "gpkg", "zip", "shp"):
        return _read_with_geopandas(suffix, name, data)
    raise GeoError(f"Formato no soportado: .{suffix}. Usá {', '.join(SUPPORTED_UPLOADS)}.")


def _read_with_geopandas(suffix: str, name: str, data: bytes) -> BaseGeometry:
    import tempfile

    import geopandas as gpd
    from shapely.ops import unary_union

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        if suffix == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tmpdir)
            shps = list(tmpdir.rglob("*.shp"))
            if not shps:
                raise GeoError("El ZIP no contiene ningún .shp.")
            target = shps[0]
        else:
            target = tmpdir / Path(name).name
            target.write_bytes(data)
        try:
            gdf = gpd.read_file(target)
        except Exception as exc:  # pragma: no cover - depende del archivo
            raise GeoError(f"No se pudo leer el archivo: {exc}") from exc

    if gdf.empty:
        raise GeoError("El archivo no contiene geometrías.")
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    geom = unary_union(list(gdf.geometry))
    return clean(geom)


def to_geojson_feature(geom: BaseGeometry | dict, props: dict[str, Any] | None = None) -> dict:
    return {"type": "Feature", "geometry": to_geojson(geom), "properties": props or {}}


def to_feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}
