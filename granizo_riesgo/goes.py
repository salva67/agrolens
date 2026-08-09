"""Acceso a las colecciones GOES ABI (MCMIP) en Earth Engine, ya calibradas a Kelvin."""

from __future__ import annotations

import datetime as dt
import warnings

import ee

# Bandas usadas. MCMIP entrega las 16 bandas ABI como CMI_C01..CMI_C16.
BANDA_IR = "CMI_C13"       # 10.3 um  ventana IR limpia -> temperatura de tope
BANDA_WV = "CMI_C08"       # 6.2 um   vapor de agua alto -> deteccion de overshooting top
BANDA_IR2 = "CMI_C14"      # 11.2 um  ventana IR
BANDA_SPLIT = "CMI_C15"    # 12.3 um  ventana sucia -> split window (espesor optico)

BANDAS = [BANDA_IR, BANDA_WV, BANDA_IR2, BANDA_SPLIT]

# GOES-East: GOES-16 hasta abril 2025, GOES-19 desde entonces.
# El corte no es un instante limpio (hubo periodo de solapamiento durante la
# transicion de abril 2025), asi que probamos en orden y usamos la primera
# coleccion que devuelva escenas para la ventana pedida.
TRANSICION_GOES19 = dt.date(2025, 4, 4)

COLECCIONES_EAST = {
    "GOES-16": "NOAA/GOES/16/MCMIPF",
    "GOES-19": "NOAA/GOES/19/MCMIPF",
}
COLECCIONES_WEST = {
    "GOES-17": "NOAA/GOES/17/MCMIPF",
    "GOES-18": "NOAA/GOES/18/MCMIPF",
}


def elegir_coleccion(fecha: dt.date, hemisferio: str = "east") -> list:
    """Orden de preferencia de colecciones para una fecha dada.

    Devuelve una lista de (nombre_satelite, id_coleccion). El llamador prueba en
    orden y se queda con la primera que tenga imagenes.
    """
    if hemisferio.lower() == "west":
        if fecha >= dt.date(2023, 1, 4):
            orden = ["GOES-18", "GOES-17"]
        else:
            orden = ["GOES-17", "GOES-18"]
        return [(n, COLECCIONES_WEST[n]) for n in orden]

    if fecha >= TRANSICION_GOES19:
        orden = ["GOES-19", "GOES-16"]
    else:
        orden = ["GOES-16", "GOES-19"]
    return [(n, COLECCIONES_EAST[n]) for n in orden]


def _escalar_banda(img: "ee.Image", banda: str) -> "ee.Image":
    """Aplica scale/offset de la banda para pasar de cuentas digitales a Kelvin.

    Las imagenes MCMIP traen las propiedades <banda>_scale y <banda>_offset.
    Si faltaran, se usa scale=1 / offset=0 (la banda ya vendria en unidades
    fisicas) para no romper la cadena.
    """
    escala = ee.Number(ee.Algorithms.If(img.get(banda + "_scale"), img.get(banda + "_scale"), 1))
    offset = ee.Number(ee.Algorithms.If(img.get(banda + "_offset"), img.get(banda + "_offset"), 0))
    return img.select(banda).multiply(escala).add(offset).rename(banda)


def calibrar(img: "ee.Image", bandas: list = None) -> "ee.Image":
    """Imagen con las bandas pedidas en Kelvin, conservando system:time_start."""
    bandas = bandas or BANDAS
    partes = [_escalar_banda(img, b) for b in bandas]
    return ee.Image.cat(partes).copyProperties(img, ["system:time_start"])


def coleccion_calibrada(
    inicio_utc: "ee.Date",
    fin_utc: "ee.Date",
    fecha_ref: dt.date,
    hemisferio: str = "east",
    bandas: list = None,
):
    """Devuelve (ImageCollection calibrada en K, nombre_satelite, id_coleccion).

    Prueba las colecciones candidatas en orden y devuelve la primera no vacia.
    Lanza RuntimeError si ninguna tiene escenas en la ventana.
    """
    bandas = bandas or BANDAS
    intentos = []
    for nombre, cid in elegir_coleccion(fecha_ref, hemisferio):
        try:
            cruda = ee.ImageCollection(cid).filterDate(inicio_utc, fin_utc)
            n = cruda.size().getInfo()
        except Exception as exc:                     # coleccion inexistente o sin permisos
            intentos.append(f"{nombre} ({cid}): {type(exc).__name__}")
            continue
        if n > 0:
            return cruda.map(lambda im: calibrar(im, bandas)), nombre, cid
        intentos.append(f"{nombre} ({cid}): 0 escenas")

    raise RuntimeError(
        "No se encontraron escenas GOES para la ventana pedida. Intentos: "
        + "; ".join(intentos)
    )


def verificar_calibracion(coleccion, geometria, escala: int = 2000) -> float:
    """Sanity check: la mediana de CMI_C13 debe caer en un rango fisico (150-350 K).

    Si el catalogo cambiara la convencion de unidades, esto avisa en vez de
    devolver silenciosamente un indice de riesgo sin sentido.
    """
    valor = (
        ee.Image(coleccion.first())
        .select(BANDA_IR)
        .reduceRegion(ee.Reducer.median(), geometria, escala, maxPixels=1e8)
        .get(BANDA_IR)
        .getInfo()
    )
    if valor is None:
        warnings.warn("No se pudo verificar la calibracion: sin pixeles validos en la region.")
        return float("nan")
    if not (150.0 <= valor <= 350.0):
        warnings.warn(
            "Calibracion sospechosa: CMI_C13 mediana = {:.1f} (esperado 150-350 K). "
            "Revisar scale/offset del catalogo antes de confiar en el indice.".format(valor)
        )
    return float(valor)
