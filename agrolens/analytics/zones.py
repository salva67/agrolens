"""Zonificación intra-lote y prescripciones.

Tres productos distintos, que la gente suele confundir:

  * **Zonas de un momento**: cluster del índice en una fecha o compuesto. Sirve
    para dirigir el monitoreo a pie de esta semana.
  * **Zonas de estabilidad**: cruce de productividad media contra variabilidad
    entre campañas. Es lo que se usa para decidir inversiones (drenaje,
    encalado, densidad variable), porque separa lo estructural de lo coyuntural.
  * **Prescripción**: la traducción de zonas a dosis, con el promedio del lote
    respetado para no romper el presupuesto de insumo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import LIGHT
from ..models import ZoneStats

ZONE_COLORS_3 = ["#c0392b", "#eda100", "#1f7522"]
ZONE_COLORS_4 = ["#a93226", "#eb6834", "#eda100", "#1f7522"]
ZONE_COLORS_5 = ["#a93226", "#eb6834", "#eda100", "#65a83a", "#0d5c1c"]

ZONE_NAMES = {
    3: ["Baja productividad", "Productividad media", "Alta productividad"],
    4: ["Muy baja", "Baja", "Media", "Alta"],
    5: ["Muy baja", "Baja", "Media", "Alta", "Muy alta"],
    2: ["Baja productividad", "Alta productividad"],
}


def zone_colors(n: int) -> list[str]:
    return {2: [ZONE_COLORS_3[0], ZONE_COLORS_3[2]], 3: ZONE_COLORS_3,
            4: ZONE_COLORS_4, 5: ZONE_COLORS_5}.get(n, ZONE_COLORS_3)


def _denoise(values: np.ndarray, size: int = 3) -> np.ndarray:
    """Filtro de mediana: quita píxeles sueltos sin correr los bordes de zona."""
    from scipy.ndimage import median_filter

    filled = np.where(np.isnan(values), np.nanmedian(values), values)
    out = median_filter(filled, size=size)
    return np.where(np.isnan(values), np.nan, out)


def management_zones(raster: dict, n_zones: int = 3, denoise: bool = True,
                     min_zone_px: int = 10) -> dict:
    """Agrupa los píxeles del lote en zonas ordenadas de menor a mayor índice."""
    from sklearn.cluster import KMeans

    values = np.asarray(raster["values"], dtype="float32")
    v = _denoise(values) if denoise else values
    mask = np.isfinite(v)
    if mask.sum() < max(50, n_zones * min_zone_px):
        raise ValueError("El lote tiene muy pocos píxeles válidos para zonificar.")

    x = v[mask].reshape(-1, 1)
    km = KMeans(n_clusters=n_zones, n_init=10, random_state=42).fit(x)

    # Reetiquetado: la zona 0 es SIEMPRE la de menor índice. Sin esto, los
    # colores cambiarían de significado en cada corrida.
    order = np.argsort(km.cluster_centers_.ravel())
    remap = np.zeros(n_zones, dtype=int)
    remap[order] = np.arange(n_zones)

    labels = np.full(v.shape, -1, dtype=int)
    labels[mask] = remap[km.labels_]

    px_area_ha = _pixel_area_ha(raster)
    names = ZONE_NAMES.get(n_zones, [f"Zona {i + 1}" for i in range(n_zones)])
    colors = zone_colors(n_zones)
    total = int(mask.sum())

    stats: list[ZoneStats] = []
    for z in range(n_zones):
        sel = labels == z
        vals = v[sel]
        stats.append(ZoneStats(
            zone=z, label=names[z] if z < len(names) else f"Zona {z + 1}",
            area_ha=round(float(sel.sum() * px_area_ha), 2),
            pct=round(100 * float(sel.sum()) / total, 1),
            mean=float(np.nanmean(vals)) if vals.size else float("nan"),
            std=float(np.nanstd(vals)) if vals.size else 0.0,
            color=colors[z] if z < len(colors) else LIGHT.s(z),
        ))

    return {
        "labels": labels, "stats": stats, "n_zones": n_zones,
        "transform": raster["transform"], "crs": raster["crs"],
        "index": raster.get("index", "NDVI"),
        "separacion": _separation(stats),
    }


def _pixel_area_ha(raster: dict) -> float:
    t = raster["transform"]
    if str(raster.get("crs", "")).upper().endswith("4326"):
        # grados: aproximamos con la latitud del centro
        import math

        lat = t.f
        m_per_deg = 111_320.0
        return abs(t.a * m_per_deg * math.cos(math.radians(lat)) * t.e * m_per_deg) / 10_000
    return abs(t.a * t.e) / 10_000


def _separation(stats: list[ZoneStats]) -> float:
    """Cuán distinguibles son las zonas: distancia entre medias sobre desvío interno."""
    means = [s.mean for s in stats if np.isfinite(s.mean)]
    stds = [s.std for s in stats if np.isfinite(s.std)]
    if len(means) < 2:
        return 0.0
    gaps = np.diff(sorted(means))
    return float(np.mean(gaps) / (np.mean(stds) + 1e-9))


def zone_polygons(zones: dict, simplify_m: float = 5.0):
    """Vectoriza las zonas a polígonos en EPSG:4326, listos para exportar."""
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape as shp_shape

    labels = zones["labels"].astype("int16")
    mask = labels >= 0
    geoms, values = [], []
    for geom, val in shapes(labels, mask=mask, transform=zones["transform"]):
        geoms.append(shp_shape(geom))
        values.append(int(val))

    gdf = gpd.GeoDataFrame({"zona": values}, geometry=geoms, crs=zones["crs"])
    gdf = gdf.dissolve(by="zona", as_index=False)
    if simplify_m and not str(zones["crs"]).endswith("4326"):
        gdf["geometry"] = gdf.geometry.simplify(simplify_m)

    by_zone = {s.zone: s for s in zones["stats"]}
    gdf["etiqueta"] = gdf["zona"].map(lambda z: by_zone[z].label if z in by_zone else "")
    gdf["indice_medio"] = gdf["zona"].map(lambda z: round(by_zone[z].mean, 3) if z in by_zone else None)
    gdf["area_ha"] = gdf["zona"].map(lambda z: by_zone[z].area_ha if z in by_zone else None)
    gdf["color"] = gdf["zona"].map(lambda z: by_zone[z].color if z in by_zone else "#888888")
    return gdf.to_crs(4326)


# --------------------------------------------------------------------------
# Estabilidad entre campañas
# --------------------------------------------------------------------------
STABILITY_CLASSES = [
    ("Alta y estable", "#0d5c1c", "El potencial del lote. Es donde conviene apuntar la inversión."),
    ("Alta e inestable", "#96bf4e", "Rinde bien en años buenos; depende del agua o del manejo."),
    ("Baja e inestable", "#eda100", "Zona de riesgo: se cae ante cualquier estrés. Revisar causa."),
    ("Baja y estable", "#a93226", "Limitante estructural (suelo, drenaje, salinidad). Ambiente aparte."),
]


def stability_zones(rasters: list[dict]) -> dict:
    """Cruza productividad media y variabilidad entre campañas.

    Requiere al menos tres campañas para que la variabilidad signifique algo.
    """
    if len(rasters) < 2:
        raise ValueError("Hacen falta al menos dos campañas para analizar estabilidad.")

    shape = rasters[0]["values"].shape
    usable = [r for r in rasters if r["values"].shape == shape]
    if len(usable) < 2:
        raise ValueError("Las campañas no comparten la misma grilla; no se pueden cruzar.")

    stack = np.stack([_normalize(r["values"]) for r in usable])
    mean = np.nanmean(stack, axis=0)
    cv = np.nanstd(stack, axis=0)

    mask = np.isfinite(mean)
    mean_thr = float(np.nanmedian(mean[mask]))
    cv_thr = float(np.nanmedian(cv[mask]))

    labels = np.full(shape, -1, dtype=int)
    high, stable = mean >= mean_thr, cv <= cv_thr
    labels[mask & high & stable] = 0
    labels[mask & high & ~stable] = 1
    labels[mask & ~high & ~stable] = 2
    labels[mask & ~high & stable] = 3

    px = _pixel_area_ha(rasters[0])
    total = int(mask.sum()) or 1
    stats = [
        ZoneStats(zone=i, label=name, area_ha=round(float((labels == i).sum() * px), 2),
                  pct=round(100 * float((labels == i).sum()) / total, 1),
                  mean=float(np.nanmean(mean[labels == i])) if (labels == i).any() else float("nan"),
                  std=float(np.nanmean(cv[labels == i])) if (labels == i).any() else 0.0,
                  color=color)
        for i, (name, color, _) in enumerate(STABILITY_CLASSES)
    ]
    return {
        "labels": labels, "stats": stats, "n_zones": 4, "campañas": len(usable),
        "transform": rasters[0]["transform"], "crs": rasters[0]["crs"],
        "mean_map": mean, "cv_map": cv, "index": rasters[0].get("index", "NDVI"),
        "descripciones": {name: desc for name, _, desc in STABILITY_CLASSES},
    }


def _normalize(values: np.ndarray) -> np.ndarray:
    """Z-score dentro de cada campaña: compara posiciones relativas, no absolutos."""
    v = np.asarray(values, dtype="float32")
    mu, sd = np.nanmean(v), np.nanstd(v)
    return (v - mu) / (sd if sd else 1.0)


# --------------------------------------------------------------------------
# Prescripción
# --------------------------------------------------------------------------
def prescription(stats: list[ZoneStats], base_dose: float, strategy: str = "compensar",
                 spread_pct: float = 25.0, unit: str = "kg/ha") -> pd.DataFrame:
    """Convierte zonas en dosis, respetando la dosis media del lote.

    * **compensar**: más insumo en las zonas de menor índice (típico en nitrógeno
      sobre ambientes con potencial similar y limitante corregible).
    * **potenciar**: más insumo donde el cultivo ya expresa potencial (habitual
      en fósforo o densidad de siembra).
    * **uniforme**: control, para comparar contra la práctica actual.
    """
    valid = [s for s in stats if s.area_ha > 0 and np.isfinite(s.mean)]
    if not valid:
        return pd.DataFrame()

    means = np.array([s.mean for s in valid], dtype=float)
    rng = means.max() - means.min()
    rel = (means - means.mean()) / (rng if rng else 1.0)  # −0,5 … +0,5 aprox.

    if strategy == "compensar":
        factor = 1 - rel * (spread_pct / 100) * 2
    elif strategy == "potenciar":
        factor = 1 + rel * (spread_pct / 100) * 2
    else:
        factor = np.ones_like(rel)

    dose = base_dose * factor
    areas = np.array([s.area_ha for s in valid], dtype=float)
    # Reescalado para que el promedio ponderado por superficie sea la dosis base
    weighted = float((dose * areas).sum() / areas.sum())
    if weighted:
        dose = dose * (base_dose / weighted)

    return pd.DataFrame({
        "zona": [s.zone + 1 for s in valid],
        "etiqueta": [s.label for s in valid],
        "superficie_ha": areas.round(2),
        "indice_medio": means.round(3),
        f"dosis_{unit.replace('/', '_')}": dose.round(1),
        "insumo_total": (dose * areas).round(0),
        "color": [s.color for s in valid],
    })


def prescription_geojson(gdf, presc: pd.DataFrame, dose_col: str | None = None) -> dict:
    """Une geometría de zonas con la dosis: el archivo que entra al monitor."""
    dose_col = dose_col or [c for c in presc.columns if c.startswith("dosis_")][0]
    lookup = dict(zip(presc["zona"] - 1, presc[dose_col]))
    out = gdf.copy()
    out["dosis"] = out["zona"].map(lookup)
    return out.__geo_interface__
