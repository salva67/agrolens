"""Ejemplo de uso desde Python (equivalente al script de Code Editor, automatizado).

    python granizo_riesgo/ejemplo.py
"""

import ee

import granizo_riesgo as gr

ee.Initialize(project="ee-salvalrc")

# --------------------------------------------------------------------------- #
# 1. Un punto y una fecha
# --------------------------------------------------------------------------- #
res = gr.evaluar_punto(
    lat=-31.42,
    lon=-64.50,
    fecha="2018-02-08",
    hora_inicio="12:00",
    hora_fin="24:00",
    radio_km=20,
    id_lote="VillaCarlosPaz",
)

r = res["resumen_punto"]
print("{}: {} / 100  ({})".format(r["lote_id"], r["score"], r["categoria"]))
print("  tope mas frio      : {} C".format(r["indicadores"]["bt_min_c"]))
print("  area < 215 K       : {:.0%}".format(r["indicadores"]["frac_area_lt215k"]))
print("  overshooting (WV-IR): {}".format(r["indicadores"]["btd_wv_ir_max"]))
print("  minutos < 225 K    : {}".format(r["indicadores"]["duracion_lt225k_min"]))
print("  momento del pico   : {}".format(r["pico"]["t_local"]))
print("  desplaz. paralaje  : {} km".format(r["paralaje_km"]))

# Imagen de respaldo para el informe
gr.api.quicklook(res, "VillaCarlosPaz", "pico_vcp.png")

# Mapa interactivo con el raster de exposicion.
# Nota: para el mapa conviene acotar la ventana a las horas de la tormenta; sobre
# 24 h el indice satura en "Muy alto" en toda la region.
gr.mapa_folium(res, "mapa_vcp.html")

# --------------------------------------------------------------------------- #
# 2. Varios lotes desde un asset de Earth Engine
# --------------------------------------------------------------------------- #
lotes = gr.lotes_desde_asset("projects/ee-salvalrc/assets/mirabet1")
res2 = gr.evaluar_lotes(lotes, fecha="2025-12-20", radio_km=20)

for r in res2["resumen"]:
    print("{:<20} {:>6.1f}  {}".format(r["lote_id"], r["score"], r["categoria"]))

# --------------------------------------------------------------------------- #
# 3. La serie temporal completa, para graficar o exportar
# --------------------------------------------------------------------------- #
import pandas as pd

df = pd.DataFrame(res["serie"])
print(df[["t_local", "bt_min_k", "f215", "btd_wv_ir_max", "score_escena"]].tail(12))
