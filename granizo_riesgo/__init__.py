"""Estimación de riesgo de exposición a granizo a partir de GOES ABI (Google Earth Engine).

Uso rápido:

    import ee, granizo_riesgo as gr
    ee.Initialize(project='ee-salvalrc')
    res = gr.evaluar_punto(lat=-33.12, lon=-63.45, fecha='2025-12-20')
    print(res['resumen'])
"""

from .api import evaluar_punto, evaluar_lotes, lotes_desde_csv, lotes_desde_asset
from .riesgo import PESOS, UMBRALES, categoria
from .goes import elegir_coleccion, verificar_calibracion
from .paralaje import corregir_paralaje
from .mapa import mapa_folium, indice_raster

__all__ = [
    "evaluar_punto",
    "evaluar_lotes",
    "lotes_desde_csv",
    "lotes_desde_asset",
    "mapa_folium",
    "indice_raster",
    "corregir_paralaje",
    "elegir_coleccion",
    "verificar_calibracion",
    "categoria",
    "PESOS",
    "UMBRALES",
]

__version__ = "0.1.0"
