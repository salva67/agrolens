"""Funciones de alto nivel: de (fecha, lat, lon) a un puntaje de exposicion a granizo."""

from __future__ import annotations

import csv
import datetime as dt
import os
import urllib.request

import ee

from . import goes, metricas, riesgo
from .paralaje import LON_GOES_EAST, LON_GOES_WEST, angulo_cenital_satelite, corregir_paralaje, desplazamiento_km

RADIO_KM_DEFECTO = 20.0
ALTURA_NUBE_KM_DEFECTO = 13.0
TZ_OFFSET_DEFECTO = -3.0        # Argentina, UTC-3 todo el año


# --------------------------------------------------------------------------- #
# Ventana temporal
# --------------------------------------------------------------------------- #

def _parse_hora(hhmm: str) -> tuple:
    """'HH:MM' -> (dias_extra, hora, minuto). Acepta '24:00' como fin del dia."""
    h, m = hhmm.split(":")
    h, m = int(h), int(m)
    if h == 24 and m == 0:
        return 1, 0, 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Hora invalida: {}".format(hhmm))
    return 0, h, m


def ventana_utc(
    fecha: str,
    fecha_fin: str = None,
    hora_inicio: str = "00:00",
    hora_fin: str = "24:00",
    tz_offset: float = TZ_OFFSET_DEFECTO,
) -> tuple:
    """Convierte una ventana horaria local a (inicio_utc, fin_utc) como datetime aware."""
    tz = dt.timezone(dt.timedelta(hours=tz_offset))
    d0 = dt.date.fromisoformat(fecha)
    d1 = dt.date.fromisoformat(fecha_fin) if fecha_fin else d0
    if d1 < d0:
        raise ValueError("fecha_fin es anterior a fecha.")

    di, hi, mi = _parse_hora(hora_inicio)
    df, hf, mf = _parse_hora(hora_fin)

    inicio = dt.datetime.combine(d0 + dt.timedelta(days=di), dt.time(hi, mi), tzinfo=tz)
    fin = dt.datetime.combine(d1 + dt.timedelta(days=df), dt.time(hf, mf), tzinfo=tz)
    if fin <= inicio:
        raise ValueError("La ventana temporal esta vacia o invertida.")
    return inicio.astimezone(dt.timezone.utc), fin.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Entrada de lotes
# --------------------------------------------------------------------------- #

def lotes_desde_csv(ruta: str, col_id: str = None, col_lat: str = None, col_lon: str = None) -> list:
    """Lee un CSV con columnas de id, latitud y longitud decimales."""
    alias_lat = {"lat", "latitud", "latitude", "y"}
    alias_lon = {"lon", "lng", "long", "longitud", "longitude", "x"}
    alias_id = {"id", "lote", "lote_id", "nombre", "name"}

    lotes = []
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        lector = csv.DictReader(fh)
        cabeceras = {c.strip().lower(): c for c in (lector.fieldnames or [])}
        c_lat = col_lat or next((cabeceras[c] for c in cabeceras if c in alias_lat), None)
        c_lon = col_lon or next((cabeceras[c] for c in cabeceras if c in alias_lon), None)
        c_id = col_id or next((cabeceras[c] for c in cabeceras if c in alias_id), None)
        if not c_lat or not c_lon:
            raise ValueError(
                "No encontre columnas de lat/lon en {}. Cabeceras: {}".format(
                    ruta, lector.fieldnames
                )
            )
        for i, fila in enumerate(lector):
            lotes.append(
                {
                    "id": (fila.get(c_id) or "lote_{}".format(i + 1)).strip(),
                    "lat": float(str(fila[c_lat]).replace(",", ".")),
                    "lon": float(str(fila[c_lon]).replace(",", ".")),
                }
            )
    return lotes


def lotes_desde_asset(asset_id: str, prop_id: str = None) -> list:
    """Centroides de un FeatureCollection de Earth Engine (por ejemplo tu asset de lotes)."""
    fc = ee.FeatureCollection(asset_id)
    centroides = fc.map(
        lambda f: ee.Feature(f.geometry().centroid(maxError=1)).copyProperties(f)
    )
    info = centroides.getInfo()

    lotes = []
    for i, feat in enumerate(info.get("features", [])):
        coords = feat["geometry"]["coordinates"]
        props = feat.get("properties", {})
        if prop_id and prop_id in props:
            ident = props[prop_id]
        else:
            ident = next(
                (props[k] for k in ("id", "ID", "lote", "Lote", "nombre", "Name") if k in props),
                "lote_{}".format(i + 1),
            )
        lotes.append({"id": str(ident), "lat": float(coords[1]), "lon": float(coords[0])})
    return lotes


# --------------------------------------------------------------------------- #
# Motor principal
# --------------------------------------------------------------------------- #

def evaluar_lotes(
    lotes: list,
    fecha: str,
    fecha_fin: str = None,
    hora_inicio: str = "00:00",
    hora_fin: str = "24:00",
    tz_offset: float = TZ_OFFSET_DEFECTO,
    radio_km: float = RADIO_KM_DEFECTO,
    altura_nube_km: float = ALTURA_NUBE_KM_DEFECTO,
    corregir_paralaje_flag: bool = True,
    hemisferio: str = "east",
    verificar: bool = True,
) -> dict:
    """Evalua exposicion a granizo para una lista de lotes en una ventana temporal.

    `lotes`: lista de dicts con claves id, lat, lon (grados decimales, lon negativa al oeste).

    Devuelve {'meta':..., 'serie': [...], 'resumen': [...]}.
    """
    if not lotes:
        raise ValueError("La lista de lotes esta vacia.")

    inicio_utc, fin_utc = ventana_utc(fecha, fecha_fin, hora_inicio, hora_fin, tz_offset)
    sat_lon = LON_GOES_EAST if hemisferio.lower() == "east" else LON_GOES_WEST

    # Ubicacion aparente en la imagen (corrige el corrimiento del tope de nube)
    enriquecidos = []
    for lote in lotes:
        lat, lon = float(lote["lat"]), float(lote["lon"])
        if corregir_paralaje_flag:
            lat_img, lon_img = corregir_paralaje(lat, lon, altura_nube_km, sat_lon)
        else:
            lat_img, lon_img = lat, lon
        enriquecidos.append(
            {
                "id": str(lote["id"]),
                "lat": lat,
                "lon": lon,
                "lat_img": lat_img,
                "lon_img": lon_img,
                "paralaje_km": round(desplazamiento_km(lat, lon, altura_nube_km, sat_lon), 1)
                if corregir_paralaje_flag
                else 0.0,
                "cenital_sat_deg": round(angulo_cenital_satelite(lat, lon, sat_lon), 1),
            }
        )

    coleccion, satelite, coleccion_id = goes.coleccion_calibrada(
        ee.Date(inicio_utc.isoformat()),
        ee.Date(fin_utc.isoformat()),
        inicio_utc.date(),
        hemisferio=hemisferio,
    )

    bt_referencia = None
    if verificar:
        centro = ee.Geometry.Point([enriquecidos[0]["lon_img"], enriquecidos[0]["lat_img"]]).buffer(
            radio_km * 1000.0
        )
        bt_referencia = goes.verificar_calibracion(coleccion, centro)

    horas = (fin_utc - inicio_utc).total_seconds() / 3600.0
    escenas_estimadas = int(horas * 6) + 1        # disco completo cada ~10 min

    filas = []
    for bloque in metricas.bloques_de_lotes(enriquecidos, escenas_estimadas):
        aois = metricas.construir_aois(bloque, radio_km)
        filas.extend(metricas.extraer(coleccion, aois))

    filas.sort(key=lambda r: (str(r["lote_id"]), r["t_utc"]))
    riesgo.agregar_tasa_enfriamiento(filas)

    tz = dt.timezone(dt.timedelta(hours=tz_offset))
    for fila in filas:
        fila["t_local"] = fila["t_utc"].astimezone(tz)
        fila["score_escena"] = round(riesgo.puntaje_escena(fila), 1)

    resumen = []
    por_id = {l["id"]: l for l in enriquecidos}
    for lote in enriquecidos:
        r = riesgo.evaluar_serie(lote["id"], filas)
        r["lat"] = lote["lat"]
        r["lon"] = lote["lon"]
        r["paralaje_km"] = lote["paralaje_km"]
        r["cenital_sat_deg"] = lote["cenital_sat_deg"]
        if lote["cenital_sat_deg"] > 70:
            r.setdefault("advertencias", []).append(
                "Angulo cenital del satelite de {:.0f} grados: geometria muy oblicua, "
                "el paralaje residual puede superar los 20 km.".format(lote["cenital_sat_deg"])
            )
        if r.get("pico"):
            t_pico = dt.datetime.fromisoformat(r["pico"]["t_utc"])
            r["pico"]["t_local"] = t_pico.astimezone(tz).isoformat()
        resumen.append(r)

    resumen.sort(key=lambda r: r.get("score", 0.0), reverse=True)

    return {
        "meta": {
            "satelite": satelite,
            "coleccion": coleccion_id,
            "ventana_utc": [inicio_utc.isoformat(), fin_utc.isoformat()],
            "ventana_local": [
                inicio_utc.astimezone(tz).isoformat(),
                fin_utc.astimezone(tz).isoformat(),
            ],
            "tz_offset": tz_offset,
            "radio_km": radio_km,
            "altura_nube_km": altura_nube_km,
            "paralaje_corregido": corregir_paralaje_flag,
            "bt_referencia_k": bt_referencia,
            "n_lotes": len(enriquecidos),
            "n_filas": len(filas),
            "pesos": riesgo.PESOS,
            "umbrales": riesgo.UMBRALES,
        },
        "serie": filas,
        "resumen": resumen,
        "_lotes": por_id,
        "_coleccion": coleccion,
    }


def evaluar_punto(lat: float, lon: float, fecha: str, id_lote: str = "punto", **kwargs) -> dict:
    """Atajo para un solo punto. Ver `evaluar_lotes` para los parametros opcionales."""
    res = evaluar_lotes([{"id": id_lote, "lat": lat, "lon": lon}], fecha, **kwargs)
    res["resumen_punto"] = res["resumen"][0] if res["resumen"] else None
    return res


# --------------------------------------------------------------------------- #
# Imagen de apoyo (PNG del momento de maxima severidad)
# --------------------------------------------------------------------------- #

PALETA_BT = [
    "081d58", "225ea8", "41b6c4", "7fcdbb", "c7e9b4",   # 250 -> 230 K
    "ffffcc", "fed976", "fd8d3c", "e31a1c", "800026",   # 230 -> 200 K
]


def quicklook(resultado: dict, lote_id: str, ruta_salida: str, zoom_km: float = None) -> str:
    """Descarga un PNG de la temperatura de tope en el momento de maxima severidad."""
    lote = resultado["_lotes"].get(str(lote_id))
    if lote is None:
        raise KeyError("Lote {} no esta en el resultado.".format(lote_id))
    fila = next((r for r in resultado["resumen"] if r["lote_id"] == str(lote_id)), None)
    if not fila or not fila.get("pico"):
        raise ValueError("El lote {} no tiene escena de pico.".format(lote_id))

    t = dt.datetime.fromisoformat(fila["pico"]["t_utc"])
    coleccion = resultado["_coleccion"]
    img = ee.Image(
        coleccion.filterDate(
            ee.Date((t - dt.timedelta(minutes=3)).isoformat()),
            ee.Date((t + dt.timedelta(minutes=3)).isoformat()),
        ).first()
    )

    centro = ee.Geometry.Point([lote["lon_img"], lote["lat_img"]])
    zoom_km = zoom_km or resultado["meta"]["radio_km"] * 5
    region = centro.buffer(zoom_km * 1000.0).bounds()

    vis = img.select(goes.BANDA_IR).visualize(min=200, max=250, palette=list(reversed(PALETA_BT)))

    # Contorno del area efectivamente medida (ya desplazada por paralaje): sin
    # esta referencia el PNG no sirve como evidencia, porque no se ve que parte
    # de la tormenta corresponde al lote.
    aoi = ee.FeatureCollection([ee.Feature(centro.buffer(resultado["meta"]["radio_km"] * 1000.0))])
    contorno = ee.Image().byte().paint(featureCollection=aoi, color=1, width=2)
    vis = vis.blend(contorno.visualize(palette=["FFFFFF"]))

    url = vis.getThumbURL({"region": region, "dimensions": 700, "format": "png"})

    os.makedirs(os.path.dirname(os.path.abspath(ruta_salida)) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, ruta_salida)
    return ruta_salida
