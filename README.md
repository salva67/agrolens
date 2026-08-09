# granizo_riesgo

Estimación automática de **exposición a granizo** a partir de imágenes GOES ABI en Google Earth Engine.
Entrada: una fecha y coordenadas decimales. Salida: un puntaje 0–100, los indicadores que lo componen,
la serie temporal escena por escena y una imagen del momento de máxima severidad.

Reemplaza al script de Code Editor por algo reproducible, batcheable sobre muchos lotes y con las
correcciones físicas que el script no tenía.

---

## Instalación

Ya tenés todo en tu Anaconda (`earthengine-api 1.7.4`, `pandas`). Solo hace falta que el paquete esté
en el path: trabajá desde `Escritorio\claude` y el `import` funciona.

Autenticación de Earth Engine (una sola vez):

```bash
C:\Users\ad651985\AppData\Local\anaconda3\Scripts\earthengine.exe authenticate
```

En VS Code, elegí el intérprete `C:\Users\ad651985\AppData\Local\anaconda3\python.exe`.

---

## Uso

### Línea de comandos

Un punto:

```bash
python -m granizo_riesgo.cli --lat -31.42 --lon -64.50 --fecha 2018-02-08 --proyecto ee-salvalrc
```

Muchos lotes desde CSV, con imagen y JSON:

```bash
python -m granizo_riesgo.cli --lotes lotes.csv --fecha 2025-12-20 --fecha-fin 2025-12-21 --radio-km 20 --salida resultados/ --png --json --proyecto ee-salvalrc
```

Desde tu asset de lotes:

```bash
python -m granizo_riesgo.cli --asset projects/ee-salvalrc/assets/mirabet1 --fecha 2025-12-20 --proyecto ee-salvalrc
```

El CSV necesita columnas de id, latitud y longitud; reconoce `id/lote/nombre`, `lat/latitud/y`,
`lon/lng/longitud/x` sin importar mayúsculas.

### Desde Python

```python
import ee, granizo_riesgo as gr
ee.Initialize(project='ee-salvalrc')

res = gr.evaluar_punto(lat=-31.42, lon=-64.50, fecha='2018-02-08',
                       hora_inicio='12:00', hora_fin='24:00', radio_km=20)

print(res['resumen_punto']['score'], res['resumen_punto']['categoria'])
gr.api.quicklook(res, 'punto', 'pico.png')
```

`res['serie']` es una lista de dicts lista para `pd.DataFrame`, con una fila por escena.

### Salidas

| Archivo | Contenido |
|---|---|
| `resumen.csv` | Una fila por lote: score, categoría, indicadores, momento del pico |
| `serie_temporal.csv` | Una fila por lote y escena (cada ~10 min) |
| `resumen.json` | Lo anterior más toda la metadata (satélite, ventana, pesos, umbrales) |
| `pico_<lote>.png` | Temperatura de tope en el momento de máxima severidad, con el área analizada marcada |
| `mapa_granizo.html` | Mapa Folium con el raster de exposición, los lotes y la leyenda (con `--mapa`) |

---

## Mapa raster (Folium)

Con `--mapa` el índice se calcula **píxel a píxel** sobre toda la región, con los mismos pesos y
umbrales que el puntaje por lote, y se arma un HTML interactivo:

```bash
granizo-riesgo --lat -31.42 --lon -64.50 --fecha 2018-02-08 --hora-inicio 19:00 --hora-fin 22:00 --mapa --proyecto ee-salvalrc
```

Desde Python:

```python
import granizo_riesgo as gr
res = gr.evaluar_punto(lat=-31.42, lon=-64.50, fecha='2018-02-08',
                       hora_inicio='19:00', hora_fin='22:00')
gr.mapa_folium(res, 'mapa.html')
```

El mapa trae cuatro capas de Earth Engine (solo la primera encendida) y los lotes como marcadores
coloreados por categoría, con el detalle de indicadores en el popup:

1. **Exposición del lote (disco de N km)** — cada píxel resume el disco de `radio_km` a su
   alrededor, con la misma definición que el puntaje por lote. Es la capa cuyo color **coincide con
   el color del marcador**.
2. **Exposición del lote (continuo 0–100)** — lo mismo sin clasificar.
3. **Estructura de la tormenta (píxel crudo)** — cada píxel con su propio valor, sin agregar. Más
   nítida para ver dónde estuvo el núcleo, pero sistemáticamente más baja que el puntaje del lote.
4. **Temperatura mínima de tope (K)**

### Por qué hay dos versiones del raster

El puntaje de un lote resume un **disco de 20 km**: toma el píxel más frío de todo el disco, el
máximo de overshooting del disco, etc. Un raster crudo píxel a píxel mide otra cosa — solo el valor
de ese píxel — y por eso da sistemáticamente más bajo. Sobre Villa Carlos Paz la brecha era de 12.5
puntos (88.2 el lote, 75.7 el píxel), suficiente para que un lote "Alto" cayera sobre un píxel
"Moderado".

La capa por defecto aplica a cada píxel el mismo resumen sobre disco que el puntaje del lote, así
que las dos cosas coinciden. Verificado:

| Caso | Puntaje del lote | Píxel del raster (disco) | Píxel crudo |
|---|---|---|---|
| Villa Carlos Paz, ventana del evento | 88.2 Muy alto | 88.6 Muy alto | 75.7 |
| Villa Carlos Paz, día completo | 99.5 Muy alto | 99.5 Muy alto | 91.5 |
| Control de invierno | 0.0 Muy bajo | 0.0 Muy bajo | 0.0 |

El efecto visual es que la capa por disco se ve "dilatada" unos 20 km respecto de la tormenta real:
es lo correcto para leer riesgo por lote, y es la razón de conservar la capa cruda para ver dónde
estuvo realmente el núcleo.

El raster está corregido de paralaje, así que queda alineado con la posición real de los lotes en
el terreno y no con la posición aparente en la imagen del satélite.

**Acotá la ventana horaria al evento.** Con `--hora-inicio 00:00 --hora-fin 24:00` el índice toma el
máximo sobre 24 horas y satura en "Muy alto" sobre toda la región: cualquier píxel que en algún
momento del día tuvo convección profunda queda en rojo. Con una ventana de 2–4 horas alrededor de la
tormenta el mapa muestra la estructura real (núcleo severo, bordes, gradiente). Para el puntaje por
lote pasa lo contrario: ahí conviene el día completo, porque el término de duración cuenta minutos.

`--umbral-mapa` controla la exposición mínima que se dibuja (15 por defecto) y `--radio-region-km`
la extensión del raster (120 km).

---

## Cómo se calcula el índice

Sobre un disco de `radio_km` alrededor del punto, en cada escena ABI se miden:

| Indicador | Banda | Qué captura |
|---|---|---|
| `bt_min_k` | C13 (10.3 µm) | Temperatura del tope más frío. Topes más fríos ⇒ corrientes ascendentes más intensas |
| `f215_max` | C13 | Fracción del área con tope < 215 K: extensión del núcleo severo, no solo un píxel aislado |
| `ot_max` | C08 − C13 | Diferencia vapor de agua alto menos ventana IR. Valores ≥ 0 son la firma clásica de *overshooting top*: la corriente penetra la tropopausa y el vapor de agua estratosférico se calienta mientras la ventana IR ya tocó su mínimo |
| `enfriamiento` | C13 | Tasa de enfriamiento del tope (K/10 min). Un enfriamiento rápido indica una tormenta en crecimiento explosivo, no una nube madura |
| `duracion_min` | C13 | Minutos con tope < 225 K sobre el lote: distingue una celda que pasó de largo de una que se quedó |

Cada indicador se normaliza a 0–1 con una rampa lineal y se combinan con pesos:

```
bt_min 0.30 | f215 0.20 | overshooting 0.25 | enfriamiento 0.15 | duración 0.10
```

Rampas y pesos están en `riesgo.py` (`UMBRALES`, `PESOS`) y se editan sin tocar el resto del código.

**Categorías:** <15 Muy bajo · 15–30 Bajo · 30–50 Moderado · 50–70 Alto · ≥70 Muy alto.

---

## Qué corrige respecto del script de Code Editor

**1. Unidades.** Las bandas `CMI_C*` vienen cuantizadas; hay que aplicar las propiedades
`CMI_C13_scale` y `CMI_C13_offset` de cada imagen para llegar a Kelvin. Los umbrales del script
(1880, 2200, 2380) estaban en cuentas digitales, no en K, así que la escala de categorías no
correspondía a las temperaturas de la leyenda. El paquete calibra y además verifica que la mediana
caiga en un rango físico (150–350 K), avisando si el catálogo cambiara de convención.

**2. Satélite.** GOES-19 reemplazó a GOES-16 como GOES-East en abril de 2025. Consultar
`NOAA/GOES/16/MCMIPF` para el 2025-12-20 devuelve **colección vacía**. `elegir_coleccion()` resuelve
el satélite por fecha y cae al otro si el primero no tiene escenas.

**3. Paralaje.** Es la corrección que más cambia el resultado. Desde GOES-East, un tope de nube a
13 km sobre Córdoba se ve desplazado ~11 km hacia el sur-sudeste respecto de su posición real en
tierra. Sin corregir, un disco de 20 km centrado en el lote mide en buena parte la nube del vecino.
`paralaje.py` traza la recta satélite → tope y la intersecta con la superficie terrestre, y el
muestreo se hace en esa posición aparente.

**3 bis. Alineación del raster del mapa.** La corrección inversa de paralaje del raster se hace con
`ee.Image.displace` en UTM. La semántica de esa función no está documentada del todo: se midió
desplazando `ee.Image.pixelLonLat()` y resulta que el desplazamiento va en metros de la proyección
pero **el eje Y apunta al sur**. Verificado end-to-end: el sesgo de paralaje sobre Villa Carlos Paz
baja de 11.4 a 1.4 puntos de índice, y el residuo equivale a ~1 km, o sea sub-píxel en una grilla de
2 km (el desplazamiento no es múltiplo del píxel y `displace` remuestrea).

**4. `reproject()` dentro de un `map()`.** Fuerza el cómputo a una escala fija sobre toda la
colección y es la causa habitual de timeouts. Acá la proyección se maneja en el `scale` de la
reducción.

**5. Costo de cómputo.** El script hacía un `reduceRegion` por imagen más varios `getInfo()` y
`evaluate()` anidados. Acá es un `reduceRegions` por escena sobre todos los lotes a la vez, con un
solo `getInfo()` por bloque (troceado para no pasar el límite de 5000 features).

**6. La leyenda no coincidía con la imagen.** `visualize()` con `min`/`max` y una paleta de 5 colores
genera una rampa continua, no las 5 clases discretas del panel de leyenda.

---

## Validación hecha

Caso positivo: granizo gigante de **Villa Carlos Paz, 8-feb-2018** (evento documentado en la campaña
RELAMPAGO), punto −31.42, −64.50.

```
SCORE 99.5  Muy alto   BTmin -75.9 C   OT max +0.85   pico 20:45 local
```

La serie temporal muestra la secuencia física esperada: el tope baja de 240 K a 200 K, la fracción
del área bajo 215 K llega a 1.0, y la diferencia C08−C13 cruza de negativa a positiva justo en el
pico — firma de tope penetrante. El horario del pico coincide con los reportes en superficie.

Caso de control: mismo punto, **15-jun-2018** (día de invierno sin convección).

```
SCORE 0.0  Muy bajo   BTmin -29.8 C
```

---

## Limitaciones que conviene tener presentes

- **Esto es exposición, no probabilidad de granizo.** Un satélite geoestacionario ve topes de nube,
  no hidrometeoros. Hay tormentas graniceras con firma satelital modesta y topes muy fríos que no
  producen granizo en superficie. El puntaje ordena bien el riesgo relativo; no es una probabilidad
  calibrada y no debería usarse solo para resolver un siniestro.
- **Resolución.** ABI son 2 km en el nadir, pero sobre Argentina el ángulo de vista oblicuo lo
  degrada a ~4–6 km efectivos. Un lote de decenas de hectáreas es subpíxel: por eso todo se evalúa
  sobre un disco explícito de `radio_km` en vez de fingir precisión a nivel de parcela.
- **Paralaje residual.** La corrección supone una altura de tope (13 km por defecto, `--altura-nube-km`).
  Si la tormenta real tenía 16 km, quedan algunos km de error. El CLI avisa cuando el ángulo cenital
  del satélite supera los 70°.
- **Los pesos son un punto de partida**, tomados de la literatura de detección de convección severa
  por IR, no ajustados a Argentina.

## Calibración con datos propios

Si tenés denuncias de siniestro con fecha y lote, la recalibración vale mucho más que cualquier
ajuste teórico:

1. Corré el paquete sobre los lotes con granizo confirmado y sobre un conjunto de control de lotes
   sin denuncia en las mismas fechas.
2. Mirá la distribución de cada indicador en ambos grupos: los que más separan merecen más peso.
3. Ajustá `UMBRALES` y `PESOS` en `riesgo.py`, o directamente entrená una regresión logística sobre
   las columnas de `serie_temporal.csv` — con unas cientos de observaciones eso rinde bastante más
   que la suma ponderada.

Un agregado que suele mejorar la detección: densidad de rayos del GLM. No está en el catálogo de
Earth Engine, pero se puede bajar de NOAA/AWS aparte y sumarse como indicador.

---

## Estructura

```
granizo_riesgo/
  paralaje.py   geometría del satélite y corrección de paralaje
  goes.py       selección de colección por fecha, calibración a Kelvin
  metricas.py   reducción por región en el servidor, una pasada por escena
  riesgo.py     indicadores, rampas, pesos y puntaje
  mapa.py       índice píxel a píxel y mapa Folium
  api.py        orquestación, entrada de lotes, quicklook PNG
  cli.py        línea de comandos
```
