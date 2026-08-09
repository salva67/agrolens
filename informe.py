"""Genera un informe HTML autocontenido, apto para adjuntar a un correo o imprimir a PDF.

El HTML no depende de nada externo (la imagen satelital va embebida en base64),
asi que se puede adjuntar tal cual o abrir sin conexion.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import os

import ee

from . import api, metricas, riesgo
from .mapa import COLORES_CATEGORIA
from .paralaje import corregir_paralaje

# Paleta categorica validada (slots 1 y 2) — ver skill dataviz.
SERIE_ENTORNO = "#2a78d6"
SERIE_LOTE = "#eb6834"
SERIE_ENTORNO_OSC = "#3987e5"
SERIE_LOTE_OSC = "#d95926"

UMBRALES_C = [
    (-38.0, "Conveccion profunda"),
    (-48.0, "Nucleo vigoroso"),
    (-58.0, "Nucleo severo"),
]


def _serie_pixel(resultado: dict, lote_id: str) -> dict:
    """Temperatura de tope en el pixel exacto del lote, escena por escena."""
    lote = resultado["_lotes"][str(lote_id)]
    pt = ee.Geometry.Point([lote["lon_img"], lote["lat_img"]])
    met = resultado["_coleccion"].map(metricas.imagen_metricas)

    con = met.map(
        lambda im: im.set(
            "v", im.select("bt").reduceRegion(ee.Reducer.first(), pt, 2000, maxPixels=1e8).get("bt")
        ).set("t", im.date().millis())
    )
    tiempos = con.aggregate_array("t").getInfo()
    valores = con.aggregate_array("v").getInfo()

    tz = dt.timezone(dt.timedelta(hours=resultado["meta"]["tz_offset"]))
    salida = {}
    for t, v in zip(tiempos, valores):
        hora = dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc).astimezone(tz)
        salida[hora.strftime("%H:%M")] = None if v is None else round(v - 273.15, 1)
    return salida


def _minutos_bajo(serie: list, umbral: float, cadencia: float) -> float:
    return sum(1 for v in serie if v is not None and v < umbral) * cadencia


def _grafico(horas, entorno, lote, h_ini="18:00", h_fin="23:59"):
    """SVG de la evolucion del tope de nube. Dos series, un solo eje."""
    idx = [i for i, h in enumerate(horas) if h_ini <= h <= h_fin]
    if not idx:
        idx = list(range(len(horas)))
    hs = [horas[i] for i in idx]
    ent = [entorno[i] for i in idx]
    lot = [lote[i] for i in idx]

    W, H = 900, 340
    # mr tiene que alcanzar para la etiqueta de umbral mas larga ("Conveccion
    # profunda"), que se dibuja a la derecha del area de ploteo.
    ml, mr, mt, mb = 52, 134, 18, 40
    pw, ph = W - ml - mr, H - mt - mb

    validos = [v for v in ent + lot if v is not None]
    ymax = max(20.0, max(validos) + 4)
    ymin = min(-70.0, min(validos) - 4)

    def X(i):
        return ml + (pw * i / max(1, len(hs) - 1))

    def Y(v):
        return mt + ph * (ymax - v) / (ymax - ymin)

    p = []
    a = p.append
    a('<svg viewBox="0 0 {} {}" class="gr" role="img" '
      'aria-label="Temperatura del tope de nube durante la tarde-noche del evento">'.format(W, H))

    # Banda de nucleo severo
    a('<rect x="{}" y="{}" width="{}" height="{}" fill="var(--banda)"/>'.format(
        ml, Y(-58.0), pw, max(0, mt + ph - Y(-58.0))))

    # Grilla y umbrales
    for t in range(int(ymin // 10 * 10), int(ymax) + 1, 10):
        if ymin <= t <= ymax:
            a('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="var(--grid)" '
              'stroke-width="1"/>'.format(ml, Y(t), ml + pw, Y(t)))
            a('<text x="{}" y="{:.1f}" class="tick" text-anchor="end">{}</text>'.format(
                ml - 8, Y(t) + 4, t))
    for t, etq in UMBRALES_C:
        if ymin <= t <= ymax:
            a('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="var(--umbral)" '
              'stroke-width="1" stroke-dasharray="5,4"/>'.format(ml, Y(t), ml + pw, Y(t)))
            a('<text x="{}" y="{:.1f}" class="umbral">{}</text>'.format(
                ml + pw + 8, Y(t) + 4, html.escape(etq)))

    # Eje X
    a('<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="var(--eje)" stroke-width="1"/>'.format(
        ml, mt + ph, ml + pw, mt + ph))
    for i, h in enumerate(hs):
        if h.endswith(":00"):
            a('<text x="{:.1f}" y="{}" class="tick" text-anchor="middle">{}</text>'.format(
                X(i), mt + ph + 22, h))

    def linea(vals, color, ancho):
        d, dibujando = [], False
        for i, v in enumerate(vals):
            if v is None:
                dibujando = False
                continue
            d.append("{}{:.1f} {:.1f}".format("M" if not dibujando else "L", X(i), Y(v)))
            dibujando = True
        if d:
            a('<path d="{}" fill="none" stroke="{}" stroke-width="{}" '
              'stroke-linejoin="round" stroke-linecap="round"/>'.format(" ".join(d), color, ancho))

    linea(ent, "var(--s1)", 2)
    linea(lot, "var(--s2)", 2)

    # Marca del minimo del entorno
    if any(v is not None for v in ent):
        imin = min((i for i, v in enumerate(ent) if v is not None), key=lambda i: ent[i])
        a('<circle cx="{:.1f}" cy="{:.1f}" r="4.5" fill="var(--s1)" stroke="var(--surf)" '
          'stroke-width="2"/>'.format(X(imin), Y(ent[imin])))
        a('<text x="{:.1f}" y="{:.1f}" class="anot" text-anchor="middle">{} &middot; {:.1f} &deg;C</text>'.format(
            X(imin), Y(ent[imin]) - 12, hs[imin], ent[imin]))

    a('<g class="hit">')
    for i, h in enumerate(hs):
        ancho = pw / max(1, len(hs) - 1)
        a('<rect x="{:.1f}" y="{}" width="{:.1f}" height="{}" fill="transparent" '
          'data-h="{}" data-e="{}" data-l="{}"/>'.format(
              X(i) - ancho / 2, mt, ancho, ph, h,
              "s/d" if ent[i] is None else "{:.1f}".format(ent[i]),
              "s/d" if lot[i] is None else "{:.1f}".format(lot[i])))
    a("</g>")
    a("</svg>")
    return "".join(p), hs, ent, lot


# El CSS lleva porcentajes (100%), asi que se sustituye por marcadores y no con
# formateo tipo % o str.format, que chocarian con ellos.
_CSS = """
:root{--surf:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--eje:#c3c2b7;--borde:rgba(11,11,11,.10);--s1:__S1__;--s2:__S2__;
--banda:rgba(208,59,59,.07);--umbral:#c3c2b7;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){
--surf:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--eje:#383835;--borde:rgba(255,255,255,.10);--s1:__S1D__;--s2:__S2D__;
--banda:rgba(224,103,103,.12);--umbral:#383835;}}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
.hoja{max-width:920px;margin:0 auto;padding:32px 28px 48px;background:var(--surf);}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:15px;margin:32px 0 10px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink2)}
.sub{color:var(--ink2);font-size:13.5px;margin:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 24px;margin:14px 0 0;padding:12px 0;
border-top:1px solid var(--borde);border-bottom:1px solid var(--borde);font-size:13px;color:var(--ink2)}
.meta b{color:var(--ink);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:18px 0 0}
.tile{border:1px solid var(--borde);border-radius:10px;padding:15px 17px}
.tile .lbl{font-size:12.5px;color:var(--ink2);margin-bottom:7px}
.tile .val{font-size:31px;font-weight:650;line-height:1.05;letter-spacing:-.02em}
.tile .cat{display:inline-block;margin-top:8px;padding:3px 11px;border-radius:999px;
font-size:12.5px;font-weight:600;color:#0b0b0b}
.tile .nota{font-size:12.5px;color:var(--ink2);margin-top:9px}
.destacado{border-left:3px solid var(--s2);background:var(--plane);padding:14px 17px;
border-radius:0 8px 8px 0;margin:18px 0 0;font-size:14.5px}
figure{margin:14px 0 0}
figcaption{font-size:12.5px;color:var(--ink2);margin-top:8px}
.gr{width:100%;height:auto;display:block}
.tick{font-size:11px;fill:var(--muted)}
.umbral{font-size:11px;fill:var(--muted)}
.anot{font-size:11.5px;font-weight:600;fill:var(--ink)}
.leyenda{display:flex;gap:20px;flex-wrap:wrap;margin:8px 0 0;font-size:13px;color:var(--ink2)}
.leyenda span{display:flex;align-items:center;gap:7px}
.sw{width:15px;height:3px;border-radius:2px;display:inline-block}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--borde);vertical-align:top}
th{font-weight:600;color:var(--ink2);font-size:12.5px}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
img{max-width:100%;height:auto;border-radius:8px;border:1px solid var(--borde)}
ul{margin:8px 0 0;padding-left:20px}li{margin:5px 0}
.pie{margin-top:34px;padding-top:14px;border-top:1px solid var(--borde);
font-size:12px;color:var(--muted)}
.ph{background:rgba(237,161,0,.16);padding:1px 5px;border-radius:3px}
details{margin-top:10px;font-size:13px}summary{cursor:pointer;color:var(--ink2)}
#tt{position:fixed;pointer-events:none;background:var(--surf);border:1px solid var(--borde);
border-radius:7px;padding:7px 10px;font-size:12.5px;box-shadow:0 3px 12px rgba(0,0,0,.16);
opacity:0;transition:opacity .1s;z-index:9}
@media print{:root{--surf:#fff;--plane:#fff}body{background:#fff}
.hoja{max-width:none;padding:0}#tt{display:none}}
"""


def generar_informe(
    resultado: dict,
    ruta_salida: str,
    lote_id: str = None,
    png_pico: str = None,
    titulo: str = "Informe de exposicion a granizo",
    establecimiento: str = None,
    cliente: str = None,
    autor: str = None,
) -> str:
    """Arma el HTML del informe. Devuelve la ruta escrita.

    Los campos que no se pasan quedan como marcadores visibles para completar,
    en vez de inventarse.
    """
    resumen = resultado["resumen"]
    if lote_id is None:
        lote_id = resumen[0]["lote_id"]
    r = next(x for x in resumen if x["lote_id"] == str(lote_id))
    meta = resultado["meta"]
    lote = resultado["_lotes"][str(lote_id)]
    ind = r["indicadores"]

    serie_px = _serie_pixel(resultado, lote_id)
    horas, entorno = [], []
    for fila in resultado["serie"]:
        if fila["lote_id"] != str(lote_id):
            continue
        horas.append(fila["t_local"].strftime("%H:%M"))
        bt = fila.get("bt_min_k")
        entorno.append(None if bt is None else round(bt - 273.15, 1))
    lote_serie = [serie_px.get(h) for h in horas]

    cadencia = r.get("cadencia_min", 10.0)
    min_lote = min((v for v in lote_serie if v is not None), default=None)

    svg, hs, ent_v, lot_v = _grafico(horas, entorno, lote_serie)

    b64 = ""
    if png_pico and os.path.exists(png_pico):
        with open(png_pico, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()

    def ph(valor, texto):
        if valor:
            return html.escape(str(valor))
        return '<span class="ph">{}</span>'.format(texto)

    col = COLORES_CATEGORIA.get(r["categoria"], "#999")

    # Minutos bajo cada umbral, entorno vs lote
    filas_umbral = []
    for u, etq in UMBRALES_C:
        filas_umbral.append(
            "<tr><td>{} ({:.0f} &deg;C)</td><td class='num'>{:.0f} min</td>"
            "<td class='num'>{:.0f} min</td></tr>".format(
                html.escape(etq), u,
                _minutos_bajo(entorno, u, cadencia),
                _minutos_bajo(lote_serie, u, cadencia))
        )

    filas_datos = []
    for h, e, l in zip(hs, ent_v, lot_v):
        if e is not None and e < -20:
            filas_datos.append(
                "<tr><td>{}</td><td class='num'>{}</td><td class='num'>{}</td></tr>".format(
                    h, "s/d" if e is None else "{:.1f}".format(e),
                    "s/d" if l is None else "{:.1f}".format(l)))

    doc = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo} &middot; {fecha}</title><style>{css}</style></head><body>
<div class="hoja">

<h1>{titulo}</h1>
<p class="sub">Analisis satelital del evento reportado &middot; {fecha_larga}</p>

<div class="meta">
<div>Establecimiento: <b>{establecimiento}</b></div>
<div>Cliente: <b>{cliente}</b></div>
<div>Coordenadas: <b>{lat:.6f}, {lon:.6f}</b></div>
<div>Satelite: <b>{satelite}</b></div>
<div>Escenas analizadas: <b>{n_escenas}</b> (cada {cadencia:.0f} min)</div>
</div>

<h2>Resultado</h2>
<div class="tiles">
  <div class="tile">
    <div class="lbl">Exposicion en el entorno del lote (radio 20 km)</div>
    <div class="val">{score:.0f}<span style="font-size:17px;color:var(--ink2)"> / 100</span></div>
    <div class="cat" style="background:{color}">{categoria}</div>
    <div class="nota">Tope de nube mas frio: <b>{bt_entorno:.1f} &deg;C</b></div>
  </div>
  <div class="tile">
    <div class="lbl">Sobre la posicion exacta del lote</div>
    <div class="val">{bt_lote}<span style="font-size:17px;color:var(--ink2)"> &deg;C</span></div>
    <div class="nota">Tope de nube mas frio medido en la vertical del lote.
    <b>{min_severo:.0f} minutos</b> por debajo de -48 &deg;C.</div>
  </div>
  <div class="tile">
    <div class="lbl">Momento de maxima intensidad</div>
    <div class="val">{hora_pico}</div>
    <div class="nota">Hora local. La celda cruzo la zona entre las {ventana}.</div>
  </div>
</div>

<div class="destacado">
<b>Lectura del evento.</b> {lectura}
</div>

<h2>Evolucion del tope de nube</h2>
<figure>
{svg}
<div class="leyenda">
  <span><i class="sw" style="background:var(--s1)"></i> Entorno del lote (minimo en 20 km)</span>
  <span><i class="sw" style="background:var(--s2)"></i> Vertical del lote</span>
</div>
<figcaption>Cuanto mas frio el tope, mas profunda la nube y mas vigorosa la corriente
ascendente. La distancia entre las dos curvas es la clave del caso: mide cuanto de la
tormenta paso efectivamente por encima del lote y cuanto por el entorno.</figcaption>
</figure>

<details><summary>Ver los datos del grafico</summary>
<table><thead><tr><th>Hora local</th><th class="num">Entorno (&deg;C)</th>
<th class="num">Lote (&deg;C)</th></tr></thead><tbody>{filas_datos}</tbody></table>
</details>

<h2>Tiempo de permanencia bajo cada umbral</h2>
<table><thead><tr><th>Umbral de temperatura de tope</th>
<th class="num">Entorno (20 km)</th><th class="num">Vertical del lote</th></tr></thead>
<tbody>{filas_umbral}</tbody></table>

{bloque_imagen}

<h2>Que muestra y que no muestra este analisis</h2>
<p><b>Lo que si establece:</b></p>
<ul>
<li>Que hubo, y a que hora exacta, una tormenta con desarrollo vertical profundo en la zona.</li>
<li>Cuan intensa fue esa tormenta segun la temperatura de sus topes de nube.</li>
<li>Si el nucleo mas intenso paso por encima del lote o desplazado de el.</li>
</ul>
<p><b>Lo que no puede establecer:</b></p>
<ul>
<li><b>El satelite no ve el granizo.</b> Mide la temperatura del tope de la nube, que es un
indicador de la intensidad de la tormenta, no una deteccion directa de piedra.</li>
<li>No determina el tamano de la piedra ni la cantidad caida.</li>
<li>No confirma ni descarta la caida de granizo en superficie sobre el lote. La resolucion
efectiva del sensor sobre esta latitud es de 4 a 6 km y las franjas de granizo suelen ser mas
angostas que eso, por lo que una afectacion parcial no seria visible.</li>
</ul>
<p>Por eso este informe es un elemento de respaldo objetivo sobre las condiciones
meteorologicas del momento, y se recomienda contrastarlo con la inspeccion a campo.</p>

<h2>Metodologia</h2>
<p>Se analizaron las {n_escenas} imagenes del satelite geoestacionario {satelite} correspondientes
al {fecha_larga}, una cada {cadencia:.0f} minutos. De cada imagen se midio la temperatura de brillo
del tope de nube (canal infrarrojo de 10,3 &micro;m), la firma de topes penetrantes (diferencia
entre el canal de vapor de agua y el infrarrojo) y la tasa de enfriamiento entre imagenes
consecutivas.</p>
<p>Las mediciones estan corregidas por <b>paralaje</b>: desde la orbita geoestacionaria un tope de
nube a 13 km de altura se ve desplazado {paralaje:.0f} km respecto de su posicion real sobre el
terreno en estas coordenadas. Sin esa correccion se estaria midiendo la nube de un vecino.</p>

<div class="pie">
Elaborado por {autor} &middot; Generado el {generado}.<br>
Fuente de datos: NOAA {satelite} ABI, procesado en Google Earth Engine.
</div>
</div>
<div id="tt"></div>
<script>
(function(){{
 var tt=document.getElementById('tt');
 document.querySelectorAll('.hit rect').forEach(function(r){{
  r.addEventListener('mouseenter',function(e){{
   tt.innerHTML='<b>'+r.dataset.h+'</b><br>Entorno: '+r.dataset.e+' &deg;C<br>Lote: '+r.dataset.l+' &deg;C';
   tt.style.opacity=1;}});
  r.addEventListener('mousemove',function(e){{
   tt.style.left=(e.clientX+14)+'px';tt.style.top=(e.clientY-10)+'px';}});
  r.addEventListener('mouseleave',function(){{tt.style.opacity=0;}});
 }});
}})();
</script>
</body></html>"""

    bloque_imagen = ""
    if b64:
        bloque_imagen = (
            '<h2>Imagen satelital del momento de maxima intensidad</h2>'
            '<figure><img src="data:image/png;base64,{}" alt="Temperatura de tope de nube">'
            '<figcaption>Temperatura del tope de nube a las {} (hora local). El circulo blanco '
            'marca el area de 20 km analizada alrededor del lote. Los tonos rojos indican los '
            'topes mas frios, es decir el nucleo mas intenso de la tormenta.</figcaption>'
            "</figure>".format(b64, r["pico"]["t_local"][11:16])
        )

    ventana = _tramo_del_pico(horas, entorno, r["pico"]["t_local"][11:16])

    brecha = ind["bt_min_c"] - (min_lote if min_lote is not None else ind["bt_min_c"])
    if min_lote is not None and abs(brecha) >= 8:
        lectura = (
            "El nucleo mas intenso de la tormenta <b>no paso por la vertical del lote</b>. "
            "En el entorno de 20 km los topes alcanzaron {:.1f} &deg;C, mientras que sobre el "
            "lote no bajaron de {:.1f} &deg;C: una diferencia de {:.0f} grados. El lote quedo "
            "en el borde del sistema, no en su centro."
        ).format(ind["bt_min_c"], min_lote, abs(brecha))
    else:
        lectura = (
            "La tormenta paso <b>por encima del lote</b>: los topes medidos en la vertical del "
            "lote ({:.1f} &deg;C) son equivalentes a los del entorno ({:.1f} &deg;C), de modo que "
            "el lote estuvo dentro del nucleo del sistema y no en su periferia."
        ).format(min_lote if min_lote is not None else 0.0, ind["bt_min_c"])

    doc = doc.format(
        titulo=html.escape(titulo),
        css=(_CSS.replace("__S1__", SERIE_ENTORNO).replace("__S2__", SERIE_LOTE)
             .replace("__S1D__", SERIE_ENTORNO_OSC).replace("__S2D__", SERIE_LOTE_OSC)),
        fecha=meta["ventana_local"][0][:10],
        fecha_larga=_fecha_larga(meta["ventana_local"][0][:10]),
        establecimiento=ph(establecimiento, "completar"),
        cliente=ph(cliente, "completar"),
        lat=lote["lat"], lon=lote["lon"],
        satelite=html.escape(meta["satelite"]),
        n_escenas=r["n_escenas"],
        cadencia=cadencia,
        score=r["score"], color=col, categoria=html.escape(r["categoria"]),
        bt_entorno=ind["bt_min_c"],
        bt_lote="s/d" if min_lote is None else "{:.1f}".format(min_lote),
        min_severo=_minutos_bajo(lote_serie, -48.0, cadencia),
        hora_pico=r["pico"]["t_local"][11:16],
        ventana=ventana,
        lectura=lectura,
        svg=svg,
        filas_datos="".join(filas_datos),
        filas_umbral="".join(filas_umbral),
        bloque_imagen=bloque_imagen,
        paralaje=r.get("paralaje_km", 0),
        autor=ph(autor, "completar"),
        generado=dt.datetime.now().strftime("%d/%m/%Y"),
    )

    os.makedirs(os.path.dirname(os.path.abspath(ruta_salida)) or ".", exist_ok=True)
    with open(ruta_salida, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return ruta_salida


def _tramo_del_pico(horas: list, serie: list, hora_pico: str, umbral: float = -38.0) -> str:
    """Tramo CONTINUO de nubosidad profunda que contiene el pico.

    Tomar el minimo y el maximo de todas las escenas frias del dia seria
    enganoso: un dia con dos episodios separados se reportaria como una sola
    tormenta de varias horas.
    """
    frio = [v is not None and v < umbral for v in serie]
    try:
        ipico = horas.index(hora_pico)
    except ValueError:
        ipico = max(range(len(serie)), key=lambda i: -(serie[i] if serie[i] is not None else 99))

    if not frio[ipico]:
        return "{} y {}".format(horas[ipico], horas[ipico])

    ini = ipico
    while ini > 0 and frio[ini - 1]:
        ini -= 1
    fin = ipico
    while fin < len(frio) - 1 and frio[fin + 1]:
        fin += 1
    return "{} y {}".format(horas[ini], horas[fin])


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return "{} de {} de {}".format(d.day, _MESES[d.month - 1], d.year)
