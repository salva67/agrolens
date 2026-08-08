"""Mapas interactivos (folium).

El mapa es la herramienta principal de trabajo, no una decoración: dibujo de
lotes, búsqueda por dirección, medición, capas satelitales del propio análisis
y comparación con cortina deslizante entre dos fechas.
"""

from __future__ import annotations

import folium
from folium.plugins import Draw, Fullscreen, MeasureControl, MiniMap, MousePosition

from ..config import BASEMAPS, DEFAULT_CENTER, DEFAULT_ZOOM
from ..geo import bounds_latlon, centroid_latlon, to_geojson

FIELD_STYLE = {"color": "#ffd166", "weight": 3, "fillOpacity": 0.0, "dashArray": "6 4"}


def base_map(center: tuple[float, float] | None = None, zoom: int = DEFAULT_ZOOM,
             basemap: str = "Satélite (Esri)", draw: bool = False,
             search: bool = True, measure: bool = True, minimap: bool = False) -> folium.Map:
    """Mapa base con las herramientas de campo activadas."""
    m = folium.Map(
        location=list(center or DEFAULT_CENTER), zoom_start=zoom, tiles=None,
        control_scale=True, prefer_canvas=True,
    )
    for name, cfg in BASEMAPS.items():
        folium.TileLayer(
            tiles=cfg["url"], attr=cfg["attr"], name=name, overlay=False, control=True,
            show=(name == basemap), max_zoom=20,
        ).add_to(m)

    Fullscreen(position="topleft", title="Pantalla completa",
               title_cancel="Salir").add_to(m)
    if measure:
        MeasureControl(primary_length_unit="meters", secondary_length_unit="kilometers",
                       primary_area_unit="hectares", position="topleft").add_to(m)
    MousePosition(position="bottomleft", separator=" | ", prefix="Lat/Lon:",
                  num_digits=5).add_to(m)
    if minimap:
        MiniMap(toggle_display=True, position="bottomright").add_to(m)
    if search:
        try:
            from folium.plugins import Geocoder

            Geocoder(collapsed=True, position="topleft", add_marker=False,
                     placeholder="Buscar establecimiento o localidad").add_to(m)
        except Exception:  # la versión de folium puede no traerlo
            pass
    if draw:
        add_draw(m)
    return m


def add_draw(m: folium.Map) -> folium.Map:
    """Herramientas de dibujo: polígono libre y rectángulo, con edición."""
    Draw(
        position="topleft",
        draw_options={
            "polyline": False, "circle": False, "circlemarker": False, "marker": False,
            "polygon": {"allowIntersection": False, "showArea": True,
                        "shapeOptions": {"color": "#ffd166", "weight": 3, "fillOpacity": 0.15}},
            "rectangle": {"showArea": True,
                          "shapeOptions": {"color": "#ffd166", "weight": 3, "fillOpacity": 0.15}},
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)
    return m


def add_field(m: folium.Map, geometry, name: str = "Lote", style: dict | None = None,
              tooltip: str | None = None) -> folium.Map:
    folium.GeoJson(
        to_geojson(geometry), name=name,
        style_function=lambda _f, s=style or FIELD_STYLE: s,
        tooltip=folium.Tooltip(tooltip or name),
    ).add_to(m)
    return m


def add_tile_layer(m: folium.Map, url: str, name: str, opacity: float = 1.0,
                   show: bool = True) -> folium.Map:
    """Capa de teselas del análisis (Earth Engine)."""
    folium.TileLayer(
        tiles=url, attr="Google Earth Engine / Copernicus Sentinel-2", name=name,
        overlay=True, control=True, opacity=opacity, show=show, max_zoom=20,
    ).add_to(m)
    return m


def add_zones(m: folium.Map, gdf, name: str = "Zonas de manejo", opacity: float = 0.65) -> folium.Map:
    """Polígonos de zonas coloreados por ambiente, con tooltip informativo."""
    folium.GeoJson(
        gdf.__geo_interface__, name=name,
        style_function=lambda f, o=opacity: {
            "fillColor": f["properties"].get("color", "#888888"),
            "color": "#ffffff", "weight": 1, "fillOpacity": o,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[c for c in ("etiqueta", "area_ha", "indice_medio", "dosis") if c in gdf.columns],
            aliases=[a for a, c in (("Ambiente", "etiqueta"), ("Superficie (ha)", "area_ha"),
                                    ("Índice medio", "indice_medio"), ("Dosis", "dosis"))
                     if c in gdf.columns],
            localize=True, sticky=True,
        ),
    ).add_to(m)
    return m


def add_colorbar(m: folium.Map, ramp: list[str] | tuple[str, ...], vmin: float, vmax: float,
                 caption: str) -> folium.Map:
    import branca.colormap as cm

    colormap = cm.LinearColormap(colors=list(ramp), vmin=vmin, vmax=vmax, caption=caption)
    colormap.add_to(m)
    return m


def add_legend(m: folium.Map, items: list[tuple[str, str]], title: str = "Referencias") -> folium.Map:
    """Leyenda discreta (par color/etiqueta) anclada abajo a la derecha."""
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
        f'<span style="width:14px;height:14px;border-radius:3px;background:{color};'
        f'display:inline-block;border:1px solid rgba(255,255,255,.6)"></span>'
        f'<span>{label}</span></div>'
        for label, color in items
    )
    html = (
        '<div style="position:fixed;bottom:26px;right:12px;z-index:9999;'
        'background:rgba(252,252,251,.94);color:#0b0b0b;padding:10px 12px;border-radius:8px;'
        'font:12px system-ui,-apple-system,\'Segoe UI\',sans-serif;'
        'box-shadow:0 2px 10px rgba(0,0,0,.25);max-width:230px">'
        f'<div style="font-weight:600;margin-bottom:6px">{title}</div>{rows}</div>'
    )
    m.get_root().html.add_child(folium.Element(html))
    return m


def fit(m: folium.Map, geometry, padding: int = 20) -> folium.Map:
    m.fit_bounds(bounds_latlon(geometry), padding=(padding, padding))
    return m


def finish(m: folium.Map) -> folium.Map:
    folium.LayerControl(collapsed=True, position="topright").add_to(m)
    return m


def field_map(geometry, tile_layers: list[tuple[str, str, float]] | None = None,
              zones_gdf=None, basemap: str = "Satélite (Esri)",
              legend: list[tuple[str, str]] | None = None,
              colorbar: tuple[list[str], float, float, str] | None = None) -> folium.Map:
    """Mapa completo de un lote con sus capas de análisis."""
    m = base_map(centroid_latlon(geometry), basemap=basemap, draw=False)
    for name, url, opacity in tile_layers or []:
        add_tile_layer(m, url, name, opacity)
    if zones_gdf is not None:
        add_zones(m, zones_gdf)
    add_field(m, geometry)
    if colorbar:
        add_colorbar(m, colorbar[0], colorbar[1], colorbar[2], colorbar[3])
    if legend:
        add_legend(m, legend)
    fit(m, geometry)
    return finish(m)


def comparison_map(geometry, url_a: str, url_b: str, label_a: str, label_b: str,
                   basemap: str = "Satélite (Esri)") -> folium.Map:
    """Cortina deslizante entre dos fechas: la forma más honesta de ver un cambio."""
    from folium.plugins import SideBySideLayers

    m = base_map(centroid_latlon(geometry), basemap=basemap)
    left = folium.TileLayer(tiles=url_a, attr="Earth Engine", name=label_a, overlay=True,
                            control=False, max_zoom=20)
    right = folium.TileLayer(tiles=url_b, attr="Earth Engine", name=label_b, overlay=True,
                             control=False, max_zoom=20)
    left.add_to(m)
    right.add_to(m)
    SideBySideLayers(layer_left=left, layer_right=right).add_to(m)
    add_field(m, geometry)
    fit(m, geometry)
    return m
