"""Indice de exposicion a granizo a partir de metricas de tope de nube.

IMPORTANTE - alcance del indice
-------------------------------
Esto NO es una probabilidad calibrada de caida de granizo. Es un indice de
EXPOSICION: cuanto se parecio la nube que paso sobre el lote a la firma
satelital tipica de una tormenta granicera. Un satelite geoestacionario ve
topes de nube, no hidrometeoros; hay tormentas graniceras con firma modesta y
topes muy frios que no producen granizo en superficie.

Los pesos y umbrales de abajo son un punto de partida razonable segun la
literatura de deteccion de conveccion severa por IR (topes frios, overshooting
tops via diferencia vapor de agua menos ventana IR, tasa de enfriamiento del
tope). Para uso operativo hay que recalibrarlos contra denuncias de siniestro
propias: ver `calibracion` en el README.
"""

from __future__ import annotations

import math
import statistics

# Rampas lineales: (valor_para_0, valor_para_1). Pueden ser decrecientes.
UMBRALES = {
    "bt_min_k": (235.0, 200.0),      # tope mas frio alcanzado sobre el lote
    "f215_max": (0.02, 0.35),        # fraccion del area con tope < 215 K
    "ot_max": (-6.0, 1.0),           # max(C08 - C13); >= 0 -> overshooting top
    "enfriamiento": (-2.0, -10.0),   # K por 10 min (negativo = se enfria)
    "duracion_min": (20.0, 120.0),   # minutos con tope < 225 K sobre el lote
}

PESOS = {
    "bt_min_k": 0.30,
    "f215_max": 0.20,
    "ot_max": 0.25,
    "enfriamiento": 0.15,
    "duracion_min": 0.10,
}

CATEGORIAS = [
    (15.0, "Muy bajo"),
    (30.0, "Bajo"),
    (50.0, "Moderado"),
    (70.0, "Alto"),
    (float("inf"), "Muy alto"),
]

UMBRAL_DURACION_K = 225.0    # tope frio que cuenta para la duracion del evento
CORTE_GAP_MIN = 25.0         # gap mayor a esto rompe el calculo de tasa de enfriamiento

# El minimo de temperatura sobre un disco de decenas de km es un estadistico
# ruidoso: basta que un cirro frio entre al area para que el minimo caiga varios
# K entre escenas y simule un enfriamiento de tope explosivo. Solo se computa la
# tasa cuando la escena posterior ya tiene conveccion profunda; enfriar un tope
# caliente no es una firma de granizo.
UMBRAL_CONVECCION_K = 235.0


def rampa(x, x0: float, x1: float) -> float:
    """Normaliza x a [0, 1] entre x0 (=0) y x1 (=1). Admite x1 < x0."""
    if x is None:
        return 0.0
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(x) or x1 == x0:
        return 0.0
    return max(0.0, min(1.0, (x - x0) / (x1 - x0)))


def categoria(score: float) -> str:
    for limite, nombre in CATEGORIAS:
        if score < limite:
            return nombre
    return CATEGORIAS[-1][1]


def agregar_tasa_enfriamiento(filas: list) -> list:
    """Agrega `enfriamiento_k_10min` a cada fila (requiere filas ordenadas por lote y tiempo)."""
    previa = {}
    for fila in filas:
        lote = fila["lote_id"]
        bt = fila.get("bt_min_k")
        t = fila["t_utc"]
        tasa = None
        if lote in previa and bt is not None and bt < UMBRAL_CONVECCION_K:
            t_prev, bt_prev = previa[lote]
            dt_min = (t - t_prev).total_seconds() / 60.0
            if bt_prev is not None and 0 < dt_min <= CORTE_GAP_MIN:
                tasa = (bt - bt_prev) / dt_min * 10.0
        fila["enfriamiento_k_10min"] = tasa
        if bt is not None:
            previa[lote] = (t, bt)
    return filas


def _cadencia_min(tiempos: list) -> float:
    if len(tiempos) < 2:
        return 10.0
    difs = [
        (tiempos[i] - tiempos[i - 1]).total_seconds() / 60.0
        for i in range(1, len(tiempos))
    ]
    difs = [d for d in difs if 0 < d <= CORTE_GAP_MIN]
    return statistics.median(difs) if difs else 10.0


def puntaje_escena(fila: dict) -> float:
    """Puntaje 0-100 de una escena individual (sin termino de duracion).

    Sirve para armar la serie temporal y ubicar el momento de maxima severidad.
    Los pesos se renormalizan al excluir la duracion.
    """
    sub = {
        "bt_min_k": rampa(fila.get("bt_min_k"), *UMBRALES["bt_min_k"]),
        "f215_max": rampa(fila.get("f215"), *UMBRALES["f215_max"]),
        "ot_max": rampa(fila.get("btd_wv_ir_max"), *UMBRALES["ot_max"]),
        "enfriamiento": rampa(fila.get("enfriamiento_k_10min"), *UMBRALES["enfriamiento"]),
    }
    peso_total = sum(PESOS[k] for k in sub)
    return 100.0 * sum(PESOS[k] * v for k, v in sub.items()) / peso_total


def evaluar_serie(lote_id: str, filas: list) -> dict:
    """Resume la ventana completa de un lote en indicadores + puntaje de riesgo."""
    filas = [f for f in filas if f["lote_id"] == lote_id]
    validas = [f for f in filas if f.get("bt_min_k") is not None]

    if not validas:
        return {
            "lote_id": lote_id,
            "n_escenas": len(filas),
            "score": 0.0,
            "categoria": "Sin datos",
            "advertencias": ["No hay pixeles GOES validos sobre el lote en la ventana."],
        }

    tiempos = [f["t_utc"] for f in validas]
    cadencia = _cadencia_min(tiempos)

    bt_min = min(f["bt_min_k"] for f in validas)
    f215_max = max((f.get("f215") or 0.0) for f in validas)
    f205_max = max((f.get("f205") or 0.0) for f in validas)
    ots = [f["btd_wv_ir_max"] for f in validas if f.get("btd_wv_ir_max") is not None]
    ot_max = max(ots) if ots else None
    tasas = [f["enfriamiento_k_10min"] for f in validas if f.get("enfriamiento_k_10min") is not None]
    enfriamiento = min(tasas) if tasas else None
    n_frias = sum(1 for f in validas if f["bt_min_k"] < UMBRAL_DURACION_K)
    duracion = n_frias * cadencia

    indicadores = {
        "bt_min_k": bt_min,
        "f215_max": f215_max,
        "ot_max": ot_max,
        "enfriamiento": enfriamiento,
        "duracion_min": duracion,
    }
    sub = {k: rampa(v, *UMBRALES[k]) for k, v in indicadores.items()}
    score = 100.0 * sum(PESOS[k] * sub[k] for k in PESOS)

    # Momento de maxima severidad instantanea
    puntajes = [(puntaje_escena(f), f) for f in validas]
    mejor_score, mejor_fila = max(puntajes, key=lambda par: par[0])

    advertencias = []
    n_pix = statistics.median([f.get("n_pix") or 0 for f in validas])
    if n_pix < 4:
        advertencias.append(
            "Solo {:.0f} pixeles GOES en el area: ampliar radio_km para una medicion estable.".format(n_pix)
        )
    if ot_max is None:
        advertencias.append("Sin banda de vapor de agua: el termino de overshooting top quedo en 0.")
    if bt_min > 240:
        advertencias.append("No hubo conveccion profunda sobre el lote en la ventana analizada.")

    return {
        "lote_id": lote_id,
        "n_escenas": len(validas),
        "cadencia_min": cadencia,
        "score": round(score, 1),
        "categoria": categoria(score),
        "indicadores": {
            "bt_min_k": round(bt_min, 1),
            "bt_min_c": round(bt_min - 273.15, 1),
            "frac_area_lt215k": round(f215_max, 3),
            "frac_area_lt205k": round(f205_max, 3),
            "btd_wv_ir_max": round(ot_max, 2) if ot_max is not None else None,
            "enfriamiento_k_10min": round(enfriamiento, 2) if enfriamiento is not None else None,
            "duracion_lt225k_min": round(duracion, 0),
        },
        "subpuntajes": {k: round(v, 3) for k, v in sub.items()},
        "pico": {
            "t_utc": mejor_fila["t_utc"].isoformat(),
            "score_escena": round(mejor_score, 1),
            "bt_min_k": round(mejor_fila["bt_min_k"], 1),
        },
        "advertencias": advertencias,
    }
