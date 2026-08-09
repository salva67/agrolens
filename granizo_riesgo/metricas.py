"""Extraccion de metricas de tope de nube por escena y por lote.

Todo el calculo pesado ocurre del lado del servidor (una reduceRegions por
escena sobre la coleccion completa de lotes) y se baja una sola vez por bloque.
"""

from __future__ import annotations

import datetime as dt

import ee

from .goes import BANDA_IR, BANDA_WV, BANDA_SPLIT

# Umbrales de temperatura de brillo (K) para fracciones de area.
# 235 K ~ tope convectivo profundo; 215 K ~ nucleo severo; 205 K ~ cercano o por
# encima de la tropopausa en latitudes medias durante el verano austral.
UMBRALES_BT = [235, 225, 215, 205]

ESCALA_M = 2000          # resolucion nominal de las bandas IR de ABI
MAX_ESCENAS_POR_BLOQUE = 4000   # limite practico de getInfo sobre FeatureCollection

def reductor():
    """min + mean + max + count en una sola pasada, compartiendo entradas.

    Se construye de forma perezosa: instanciar objetos ee.* en tiempo de import
    obligaria a llamar a ee.Initialize() antes del `import`, lo que rompe el uso
    normal del paquete.
    """
    return (
        ee.Reducer.min()
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.max(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )


def imagen_metricas(img: "ee.Image") -> "ee.Image":
    """Arma la imagen multibanda que se reduce por region.

    bandas:
      bt          temperatura de brillo del tope (K, C13)
      btd_wv_ir   C08 - C13. Valores >= 0 indican overshooting top: el vapor de
                  agua estratosferico se calienta mientras la ventana IR ya toco
                  el minimo, firma clasica de conveccion que penetra la tropopausa.
      btd_split   C13 - C15. Cercano a 0 en nubes de hielo opticamente gruesas.
      f235..f205  mascaras binarias; su media sobre el area da la fraccion cubierta.
    """
    bt = img.select(BANDA_IR).rename("bt")
    btd_wv_ir = img.select(BANDA_WV).subtract(img.select(BANDA_IR)).rename("btd_wv_ir")
    btd_split = img.select(BANDA_IR).subtract(img.select(BANDA_SPLIT)).rename("btd_split")

    fracciones = [bt.lt(u).rename("f{}".format(u)) for u in UMBRALES_BT]
    return ee.Image.cat([bt, btd_wv_ir, btd_split] + fracciones).copyProperties(
        img, ["system:time_start"]
    )


def _stats_por_escena(img: "ee.Image", aois: "ee.FeatureCollection") -> "ee.FeatureCollection":
    m = ee.Image(imagen_metricas(img))
    t = ee.Image(img).date().millis()
    stats = m.reduceRegions(
        collection=aois,
        reducer=reductor(),
        scale=ESCALA_M,
        tileScale=2,
    )
    # Sin geometria: baja el peso de la respuesta de forma notable.
    return stats.map(lambda f: ee.Feature(f).setGeometry(None).set("t_ms", t))


_PROPS = (
    ["lote_id", "t_ms", "bt_min", "bt_mean", "bt_count", "btd_wv_ir_max", "btd_split_min"]
    + ["f{}_mean".format(u) for u in UMBRALES_BT]
)


def extraer(coleccion, aois: "ee.FeatureCollection") -> list:
    """Baja las metricas de todas las escenas para todos los lotes de `aois`.

    Devuelve una lista de dicts planos, uno por (lote, escena).
    """
    fc = ee.FeatureCollection(coleccion.map(lambda im: _stats_por_escena(im, aois))).flatten()
    fc = fc.select(_PROPS, retainGeometry=False)
    crudo = fc.getInfo()

    filas = []
    for feat in crudo.get("features", []):
        p = feat.get("properties", {})
        if p.get("t_ms") is None:
            continue
        filas.append(
            {
                "lote_id": p.get("lote_id"),
                "t_utc": dt.datetime.fromtimestamp(p["t_ms"] / 1000.0, tz=dt.timezone.utc),
                "bt_min_k": p.get("bt_min"),
                "bt_mean_k": p.get("bt_mean"),
                "n_pix": p.get("bt_count"),
                "btd_wv_ir_max": p.get("btd_wv_ir_max"),
                "btd_split_min": p.get("btd_split_min"),
                **{"f{}".format(u): p.get("f{}_mean".format(u)) for u in UMBRALES_BT},
            }
        )
    filas.sort(key=lambda r: (str(r["lote_id"]), r["t_utc"]))
    return filas


def construir_aois(lotes: list, radio_km: float) -> "ee.FeatureCollection":
    """FeatureCollection de circulos alrededor de cada punto (ya con paralaje aplicado).

    Se usa siempre un buffer circular, incluso si el lote original era un poligono:
    a la resolucion efectiva de ABI sobre Argentina (~4-6 km por el angulo de
    vision oblicuo) un lote de decenas de hectareas es subpixel, y la incerteza
    residual de paralaje es del orden de varios km. Un disco de radio explicito
    es mas honesto que fingir precision a nivel de parcela.
    """
    feats = []
    for lote in lotes:
        pt = ee.Geometry.Point([lote["lon_img"], lote["lat_img"]])
        feats.append(ee.Feature(pt.buffer(radio_km * 1000.0), {"lote_id": str(lote["id"])}))
    return ee.FeatureCollection(feats)


def bloques_de_lotes(lotes: list, escenas_estimadas: int) -> list:
    """Parte la lista de lotes para que cada getInfo quede por debajo del limite."""
    escenas_estimadas = max(1, escenas_estimadas)
    por_bloque = max(1, MAX_ESCENAS_POR_BLOQUE // escenas_estimadas)
    return [lotes[i : i + por_bloque] for i in range(0, len(lotes), por_bloque)]
