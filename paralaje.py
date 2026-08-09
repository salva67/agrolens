"""Corrección de paralaje para satélites geoestacionarios.

Un satélite geoestacionario ve el tope de la nube proyectado sobre la superficie
en un punto desplazado respecto de la vertical real del tope. El desplazamiento
crece con la altura de la nube y con el ángulo cenital del satélite.

Para Argentina central (~33 S, 63 O) visto desde GOES-East (75.2 O) el ángulo
cenital ronda los 45-50 grados: un tope a 13 km se ve corrido entre 13 y 18 km
hacia el sur-sudoeste (alejandose del punto subsatelital).

Este modulo resuelve la geometria exacta sobre una Tierra esferica:
dado un punto en tierra P y una altura de nube H, devuelve la coordenada
"aparente" A donde el satelite dibuja ese tope en la imagen. Ahi es donde hay
que muestrear los pixeles para que correspondan al lote real.
"""

from __future__ import annotations

import math

R_TIERRA_KM = 6371.0088          # radio medio terrestre
ALT_GEO_KM = 35786.0             # altura de la orbita geoestacionaria
LON_GOES_EAST = -75.2            # posicion nominal de GOES-East (16 y 19)
LON_GOES_WEST = -137.0           # posicion nominal de GOES-West (18)


def _a_ecef(lat_deg: float, lon_deg: float, radio_km: float) -> tuple:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return (
        radio_km * math.cos(lat) * math.cos(lon),
        radio_km * math.cos(lat) * math.sin(lon),
        radio_km * math.sin(lat),
    )


def _a_latlon(x: float, y: float, z: float) -> tuple:
    r = math.sqrt(x * x + y * y + z * z)
    return math.degrees(math.asin(z / r)), math.degrees(math.atan2(y, x))


def angulo_cenital_satelite(lat: float, lon: float, sat_lon: float = LON_GOES_EAST) -> float:
    """Angulo cenital del satelite visto desde (lat, lon), en grados.

    Sirve para avisar cuando la geometria de observacion es tan oblicua que la
    medicion pierde valor (>70 grados).
    """
    p = _a_ecef(lat, lon, R_TIERRA_KM)
    s = _a_ecef(0.0, sat_lon, R_TIERRA_KM + ALT_GEO_KM)
    v = [s[i] - p[i] for i in range(3)]
    norm_v = math.sqrt(sum(c * c for c in v))
    norm_p = R_TIERRA_KM
    cos_z = sum(p[i] * v[i] for i in range(3)) / (norm_p * norm_v)
    cos_z = max(-1.0, min(1.0, cos_z))
    return math.degrees(math.acos(cos_z))


def corregir_paralaje(
    lat: float,
    lon: float,
    altura_nube_km: float = 13.0,
    sat_lon: float = LON_GOES_EAST,
) -> tuple:
    """Devuelve (lat_aparente, lon_aparente) donde muestrear la imagen.

    Traza la recta satelite -> tope de nube (a `altura_nube_km` sobre el punto
    P) y la intersecta con la superficie terrestre. Ese punto de interseccion es
    la ubicacion aparente del tope en la imagen del satelite.
    """
    if altura_nube_km <= 0:
        return lat, lon

    p = _a_ecef(lat, lon, R_TIERRA_KM)
    c = _a_ecef(lat, lon, R_TIERRA_KM + altura_nube_km)   # tope, en la vertical de P
    s = _a_ecef(0.0, sat_lon, R_TIERRA_KM + ALT_GEO_KM)

    d = [c[i] - s[i] for i in range(3)]

    # |S + t*d|^2 = R^2  ->  a t^2 + b t + cc = 0
    a = sum(x * x for x in d)
    b = 2.0 * sum(s[i] * d[i] for i in range(3))
    cc = sum(x * x for x in s) - R_TIERRA_KM ** 2

    disc = b * b - 4.0 * a * cc
    if disc <= 0:
        # La linea de vista no toca la superficie: punto fuera del disco visible.
        return lat, lon

    t = (-b - math.sqrt(disc)) / (2.0 * a)      # raiz cercana al satelite
    apx = [s[i] + t * d[i] for i in range(3)]
    lat_a, lon_a = _a_latlon(*apx)

    # Fuera de rango util: devolvemos el original antes que una coordenada absurda
    if not (-90 <= lat_a <= 90) or abs(lat_a - lat) > 5 or abs(lon_a - lon) > 5:
        return lat, lon
    return lat_a, lon_a


def desplazamiento_km(
    lat: float,
    lon: float,
    altura_nube_km: float = 13.0,
    sat_lon: float = LON_GOES_EAST,
) -> float:
    """Magnitud del desplazamiento por paralaje, en km. Util para reportes."""
    lat_a, lon_a = corregir_paralaje(lat, lon, altura_nube_km, sat_lon)
    dlat = math.radians(lat_a - lat) * R_TIERRA_KM
    dlon = math.radians(lon_a - lon) * R_TIERRA_KM * math.cos(math.radians((lat + lat_a) / 2))
    return math.hypot(dlat, dlon)
