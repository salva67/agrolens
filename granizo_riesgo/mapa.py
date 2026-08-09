"""Mapa Folium con el indice de exposicion como capa raster.

A diferencia del resumen por lote (un numero por punto), aca el indice se calcula
pixel a pixel sobre toda la region, con los mismos pesos y umbrales de `riesgo.py`,
y se corrige el paralaje para que el raster quede alineado con la posicion real de
los lotes en el terreno.
"""

from __future__ import annotations

import math

import ee

from . import riesgo
from .goes import BANDA_IR
from .metricas import imagen_metricas
from .paralaje import LON_GOES_EAST, corregir_paralaje

# Mismos cortes que riesgo.CATEGORIAS, en el mismo orden.
COLORES_CATEGORIA = {
    "Muy bajo": "#2c7bb6",
    "Bajo": "#abd9e9",
    "Moderado": "#ffffbf",
    "Alto": "#fdae61",
    "Muy alto": "#d7191c",
    "Sin datos": "#999999",
}
PALETA_CLASES = ["2c7bb6", "abd9e9", "ffffbf", "fdae61", "d7191c"]

# Semantica de ee.Image.displace, medida empiricamente (no esta clara en la doc):
# el desplazamiento va en metros de la proyeccion de la imagen, y para un pixel
# de destino p la fuente se toma en (este_p + dx, norte_p - dy). Es decir el eje X
# es el habitual pero el eje Y apunta al SUR, como las filas de la imagen.
# Verificado desplazando ee.Image.pixelLonLat() en UTM 20S: dy=+20000 devuelve el
# pixel 19.9 km al sur, dx=+20000 el pixel 19.5 km al este.
_EJE_Y_AL_SUR = True


def _crs_utm(lat: float, lon: float) -> str:
    zona = int((lon + 180.0) / 6.0) + 1
    codigo = 32700 + zona if lat < 0 else 32600 + zona
    return "EPSG:{}".format(codigo)


def _rampa_ee(img: "ee.Image", x0: float, x1: float) -> "ee.Image":
    """Version servidor de riesgo.rampa: normaliza a [0, 1] entre x0 y x1."""
    return img.subtract(x0).divide(x1 - x0).clamp(0.0, 1.0)


def indice_raster(
    coleccion,
    region: "ee.Geometry",
    radio_km: float = 20.0,
    altura_nube_km: float = 13.0,
    sat_lon: float = LON_GOES_EAST,
    corregir: bool = True,
    agregado: bool = True,
) -> "ee.Image":
    """Indice de exposicion 0-100, ya corregido de paralaje.

    `agregado=True` (por defecto) reproduce EXACTAMENTE la definicion del puntaje
    por lote: cada pixel resume el disco de `radio_km` a su alrededor, igual que
    `riesgo.evaluar_serie` resume el disco del lote. Es lo que hace que el color
    del raster bajo un lote coincida con el color de su marcador.

    `agregado=False` da la version cruda pixel a pixel: mas nitida para ver la
    estructura de la tormenta, pero sistematicamente MENOR que el puntaje del
    lote, porque un pixel solo no captura el minimo de todo el disco.
    """
    metricas = coleccion.map(imagen_metricas)
    kernel = ee.Kernel.circle(radius=radio_km * 1000.0, units="meters")

    # Cadencia real (mode 3 = 15 min hasta 2019, mode 6 = 10 min despues)
    tiempos = metricas.aggregate_array("system:time_start").sort()
    n = tiempos.size()
    cadencia = ee.Number(
        ee.Algorithms.If(
            n.gt(1),
            ee.Number(tiempos.get(-1))
            .subtract(ee.Number(tiempos.get(0)))
            .divide(n.subtract(1))
            .divide(60000.0),
            10.0,
        )
    )

    if agregado:
        # Serie de "temperatura minima del disco", el mismo estadistico sobre el
        # que se calculan bt_min, duracion y enfriamiento en el puntaje por lote.
        def _focal_min(im):
            return (
                im.select("bt")
                .reduceNeighborhood(reducer=ee.Reducer.min(), kernel=kernel)
                .rename("bt")
                .toFloat()
                .copyProperties(im, ["system:time_start"])
            )

        serie_bt = metricas.map(_focal_min)

        # Fraccion del disco bajo 215 K en cada escena, y su maximo temporal:
        # identico a f215_max del puntaje por lote.
        frac = (
            metricas.map(
                lambda im: im.select("f215")
                .reduceNeighborhood(reducer=ee.Reducer.mean(), kernel=kernel)
                .rename("frac")
                .toFloat()
            )
            .max()
        )
        ot_max = (
            metricas.select("btd_wv_ir")
            .max()
            .reduceNeighborhood(reducer=ee.Reducer.max(), kernel=kernel)
            .rename("ot")
        )
    else:
        serie_bt = metricas.map(
            lambda im: im.select("bt").toFloat().copyProperties(im, ["system:time_start"])
        )
        frac = metricas.select("f215").max().rename("frac")
        ot_max = metricas.select("btd_wv_ir").max().rename("ot")

    serie_bt = ee.ImageCollection(serie_bt)
    bt_min = serie_bt.min()
    duracion = serie_bt.map(lambda im: im.lt(riesgo.UMBRAL_DURACION_K)).sum().multiply(cadencia)

    # Tasa de enfriamiento: minimo de las diferencias entre escenas consecutivas,
    # con el mismo gate de conveccion profunda que la serie por lote.
    lista = serie_bt.toList(serie_bt.size())

    def _diferencia(i):
        i = ee.Number(i)
        previa = ee.Image(lista.get(i.subtract(1)))
        actual = ee.Image(lista.get(i))
        dt_min = (
            ee.Number(actual.get("system:time_start"))
            .subtract(ee.Number(previa.get("system:time_start")))
            .divide(60000.0)
        )
        # toFloat() no es opcional: al dividir por un dt distinto en cada par,
        # Earth Engine infiere un rango de valores distinto por imagen y la
        # reduccion de la coleccion falla con "expected a homogeneous image
        # collection". Solo aparece cuando la ventana tiene escenas faltantes
        # y los dt dejan de ser uniformes.
        return (
            actual.subtract(previa)
            .divide(dt_min)
            .multiply(10.0)
            .updateMask(actual.lt(riesgo.UMBRAL_CONVECCION_K))
            .toFloat()
        )

    enfriamiento = ee.ImageCollection(
        ee.List.sequence(1, serie_bt.size().subtract(1)).map(_diferencia)
    ).min()

    sub = {
        "bt_min_k": _rampa_ee(bt_min, *riesgo.UMBRALES["bt_min_k"]),
        "f215_max": _rampa_ee(frac, *riesgo.UMBRALES["f215_max"]),
        "ot_max": _rampa_ee(ot_max, *riesgo.UMBRALES["ot_max"]),
        # unmask(0): sin conveccion profunda no hay enfriamiento valido y el
        # termino debe aportar 0, no enmascarar el pixel entero.
        "enfriamiento": _rampa_ee(
            enfriamiento.unmask(0), *riesgo.UMBRALES["enfriamiento"]
        ),
        "duracion_min": _rampa_ee(duracion, *riesgo.UMBRALES["duracion_min"]),
    }

    # Acumular con ee.ImageCollection y no partiendo de ee.Image.constant(0):
    # una imagen constante tiene proyeccion de 1 grado y se la impone al
    # resultado de la operacion, lo que degrada el raster al renderizarlo.
    # El rename es necesario: ImageCollection.sum() exige nombres de banda
    # homogeneos y cada termino llega con el nombre de su metrica de origen.
    terminos = [
        sub[clave].multiply(peso).rename("v").toFloat()
        for clave, peso in riesgo.PESOS.items()
    ]
    indice = ee.ImageCollection(terminos).sum().multiply(100.0).rename("exposicion")

    if not corregir:
        return indice.clip(region)

    # Inversa del paralaje: el raster esta en coordenadas aparentes y hay que
    # devolverlo a coordenadas de terreno para que coincida con los lotes.
    centro = region.centroid(maxError=100).coordinates().getInfo()
    lon_c, lat_c = float(centro[0]), float(centro[1])
    lat_ap, lon_ap = corregir_paralaje(lat_c, lon_c, altura_nube_km, sat_lon)

    # Cada pixel del raster corregido debe tomar su valor de la posicion aparente
    # correspondiente, o sea desplazarse (aparente - real).
    R = 6371008.8
    delta_norte = math.radians(lat_ap - lat_c) * R
    delta_este = math.radians(lon_ap - lon_c) * R * math.cos(
        math.radians((lat_c + lat_ap) / 2.0)
    )

    dx = delta_este
    dy = -delta_norte if _EJE_Y_AL_SUR else delta_norte

    crs = _crs_utm(lat_c, lon_c)
    indice = indice.reproject(crs=crs, scale=2000)
    desplazamiento = (
        ee.Image.constant([dx, dy]).rename(["dx", "dy"]).reproject(crs=crs, scale=2000)
    )
    return indice.displace(desplazamiento).clip(region)


def clasificar(indice: "ee.Image") -> "ee.Image":
    """Convierte el indice continuo en las 5 clases de riesgo.CATEGORIAS.

    Se clasifica antes de visualizar para que los colores del mapa sean
    exactamente los 5 de la leyenda. Pasar una paleta de 5 colores a visualize()
    con min/max genera una rampa continua, no clases discretas.
    """
    cortes = [c for c, _ in riesgo.CATEGORIAS[:-1]]
    comparaciones = [indice.gte(corte) for corte in cortes]
    clases = ee.ImageCollection(comparaciones).sum().rename("clase")
    return clases.updateMask(indice.mask())


# --------------------------------------------------------------------------- #
# Folium
# --------------------------------------------------------------------------- #

def _capa_ee(
    mapa,
    imagen: "ee.Image",
    vis: dict,
    nombre: str,
    opacidad: float = 0.65,
    visible: bool = True,
):
    import folium

    mapid = imagen.getMapId(vis)
    folium.raster_layers.TileLayer(
        tiles=mapid["tile_fetcher"].url_format,
        attr="Google Earth Engine / NOAA GOES",
        name=nombre,
        overlay=True,
        control=True,
        opacity=opacidad,
        show=visible,
    ).add_to(mapa)


_LEYENDA = """
{{% macro html(this, kwargs) %}}
<div style="position: fixed; bottom: 28px; left: 18px; z-index: 9999;
            background: rgba(255,255,255,.93); padding: 10px 14px;
            border: 1px solid #999; border-radius: 6px; font: 12px/1.5 sans-serif;
            box-shadow: 0 1px 4px rgba(0,0,0,.3);">
  <div style="font-weight:700; margin-bottom:6px;">Exposicion a granizo</div>
  {filas}
  <div style="margin-top:8px; color:#555; max-width:230px; font-size:11px;">
    {pie}
  </div>
</div>
{{% endmacro %}}
"""


def _leyenda(pie: str):
    from branca.element import MacroElement
    from jinja2 import Template

    filas = []
    for corte, nombre in riesgo.CATEGORIAS:
        color = COLORES_CATEGORIA[nombre]
        filas.append(
            '<div><span style="display:inline-block;width:14px;height:14px;'
            'background:{};border:1px solid #666;vertical-align:-2px;'
            'margin-right:6px;"></span>{}</div>'.format(color, nombre)
        )

    elemento = MacroElement()
    elemento._template = Template(_LEYENDA.format(filas="".join(filas), pie=pie))
    return elemento


def mapa_folium(
    resultado: dict,
    ruta_salida: str = "mapa_granizo.html",
    radio_region_km: float = 120.0,
    umbral_visible: float = 15.0,
    opacidad: float = 0.65,
):
    """Genera un HTML de Folium con el raster de exposicion y los lotes.

    `resultado` es lo que devuelve `evaluar_punto` / `evaluar_lotes`.
    """
    import folium

    meta = resultado["meta"]
    lotes = list(resultado["_lotes"].values())
    if not lotes:
        raise ValueError("El resultado no tiene lotes.")

    lat_c = sum(l["lat"] for l in lotes) / len(lotes)
    lon_c = sum(l["lon"] for l in lotes) / len(lotes)

    region = ee.Geometry.Point([lon_c, lat_c]).buffer(radio_region_km * 1000.0).bounds()

    comun = dict(
        radio_km=meta["radio_km"],
        altura_nube_km=meta["altura_nube_km"],
        corregir=meta["paralaje_corregido"],
    )
    indice = indice_raster(resultado["_coleccion"], region, agregado=True, **comun)
    indice_px = indice_raster(resultado["_coleccion"], region, agregado=False, **comun)

    clases = clasificar(indice.updateMask(indice.gte(umbral_visible)))
    clases_px = clasificar(indice_px.updateMask(indice_px.gte(umbral_visible)))

    mapa = folium.Map(location=[lat_c, lon_c], zoom_start=8, tiles=None)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(mapa)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satelital", overlay=False,
    ).add_to(mapa)

    # Solo una capa arranca encendida: superponerlas deja un mapa ilegible.
    # La primera es la agregada por disco, la unica cuyo color coincide con el
    # color del marcador de cada lote.
    vis_clases = {"min": 0, "max": len(PALETA_CLASES) - 1, "palette": PALETA_CLASES}
    _capa_ee(
        mapa,
        clases,
        vis_clases,
        "Exposicion del lote (disco de {:.0f} km)".format(meta["radio_km"]),
        opacidad,
        visible=True,
    )
    _capa_ee(
        mapa,
        indice,
        {"min": 0, "max": 100, "palette": PALETA_CLASES},
        "Exposicion del lote (continuo 0-100)",
        opacidad,
        visible=False,
    )
    _capa_ee(
        mapa,
        clases_px,
        vis_clases,
        "Estructura de la tormenta (pixel crudo)",
        opacidad,
        visible=False,
    )
    _capa_ee(
        mapa,
        resultado["_coleccion"].select(BANDA_IR).min(),
        {"min": 200, "max": 260, "palette": list(reversed(PALETA_CLASES))},
        "Temperatura minima de tope (K)",
        opacidad,
        visible=False,
    )

    grupo = folium.FeatureGroup(name="Lotes", show=True)
    por_id = {r["lote_id"]: r for r in resultado["resumen"]}
    for lote in lotes:
        r = por_id.get(lote["id"], {})
        color = COLORES_CATEGORIA.get(r.get("categoria", "Sin datos"), "#999999")
        ind = r.get("indicadores", {})
        popup = folium.Popup(
            "<b>{}</b><br>Exposicion: <b>{} / 100</b> ({})<br>"
            "Tope minimo: {} C<br>Area &lt;215 K: {}<br>"
            "Overshooting (WV-IR): {}<br>Minutos &lt;225 K: {}<br>"
            "Pico: {}<br><i>Paralaje corregido: {} km</i>".format(
                lote["id"],
                r.get("score", "-"),
                r.get("categoria", "-"),
                ind.get("bt_min_c", "-"),
                ind.get("frac_area_lt215k", "-"),
                ind.get("btd_wv_ir_max", "-"),
                ind.get("duracion_lt225k_min", "-"),
                (r.get("pico") or {}).get("t_local", "-"),
                lote.get("paralaje_km", 0),
            ),
            max_width=320,
        )
        folium.Circle(
            location=[lote["lat"], lote["lon"]],
            radius=meta["radio_km"] * 1000.0,
            color="#222", weight=1, fill=False, dash_array="4,4",
        ).add_to(grupo)
        folium.CircleMarker(
            location=[lote["lat"], lote["lon"]],
            radius=7, color="#222", weight=1.5,
            fill=True, fill_color=color, fill_opacity=1.0,
            popup=popup, tooltip="{}: {}".format(lote["id"], r.get("score", "-")),
        ).add_to(grupo)
    grupo.add_to(mapa)

    pie = "{} &middot; {} a {} (local)<br>Radio de analisis: {} km".format(
        meta["satelite"],
        meta["ventana_local"][0][:16].replace("T", " "),
        meta["ventana_local"][1][:16].replace("T", " "),
        meta["radio_km"],
    )
    mapa.get_root().add_child(_leyenda(pie))
    folium.LayerControl(collapsed=False).add_to(mapa)

    mapa.save(ruta_salida)
    return ruta_salida
