# AgroLens

Plataforma de monitoreo agrícola satelital y agroclimático. Dibujás un lote y
obtenés su estado actual, su historia, sus ambientes internos y un informe
accionable — con la evidencia y el método a la vista.

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Qué hace

| | |
|---|---|
| **Vegetación** | 12 índices espectrales sobre Sentinel-2 a 10 m, curva reconstruida y suavizada, fenología automática (inicio, pico y fin de temporada), uniformidad interna, mapa y comparación de dos fechas con cortina deslizante. |
| **Clima y agua** | Serie ERA5 diaria + pronóstico a 16 días, normales 1991–2020 del mismo punto, balance hídrico FAO-56 con Kc derivado del NDVI observado, grados-día con etapas fenológicas, rachas secas, heladas, golpes de calor y ventanas de piso para entrar con la máquina. |
| **Ambientes** | Zonificación por k-medias con etiquetas estables, mapa de estabilidad entre campañas (lo estructural contra lo coyuntural) y prescripción de dosis variable que respeta el promedio del lote. |
| **Historia** | La campaña actual contra las anteriores del mismo lote, alineadas por días desde la siembra, con banda de percentiles y anomalía diaria. |
| **Salidas** | Informe PDF, libro de Excel con todas las tablas, GeoJSON, shapefile, GeoTIFF y paquete de prescripción listo para el monitor. |

Todo el análisis desemboca en un **motor de alertas**: reglas explícitas que
producen hallazgos ordenados por severidad, cada uno con su umbral y con qué
hacer al respecto.

---

## Qué cambia respecto de la app que lo inspiró

| Aspecto | Antes | AgroLens |
|---|---|---|
| Acceso | Código por email para usar el "MVP libre" | Sin registro ni verificación |
| Índices | NDVI libre; GNDVI y otros "reservados" | Los 12 disponibles siempre |
| Lotes | Se redibujan en cada visita | Se guardan en SQLite; se importan de KML/KMZ/SHP/GPKG/GeoJSON |
| Nubes | Filtro por escena | s2cloudless + SCL, dilatación de 60 m y control de píxeles válidos por lote |
| Serie | Puntos observados | Filtro de residuos de nube, grilla diaria y Savitzky-Golay |
| Lluvia | GPM | ERA5 horario + pronóstico + normales 1991–2020 |
| Agua | — | Balance FAO-56 con Kc satelital, Ks diario y penalidad por ventana crítica |
| Intra-lote | — | Zonas, estabilidad plurianual, histograma y prescripción |
| Historia | Comparación básica | Percentiles por día desde siembra sobre N campañas |
| Salida | PDF | PDF + Excel + GeoJSON + SHP + GeoTIFF + paquete VRA |
| Sin conexión | Falla | Modo demostración con datos sintéticos, siempre señalizado |
| Velocidad | Recalcula todo | Caché en disco persistente entre sesiones |
| Pruebas | — | 41 pruebas automáticas sin red |

---

## Instalación

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt
```

Para el mapa interactivo y el informe PDF hacen falta dos paquetes que suelen
faltar en una instalación base:

```bash
pip install streamlit-folium reportlab
```

> Los gráficos del PDF se dibujan con matplotlib, no con la exportación de
> Plotly: `kaleido` se cuelga en Windows con cierta frecuencia y un informe que
> nunca termina es peor que uno sin gráficos.

### Earth Engine

Las imágenes salen de Google Earth Engine, que es gratuito para uso no
comercial pero pide una cuenta y un proyecto.

```bash
pip install earthengine-api
earthengine authenticate
```

Después indicá el proyecto por variable de entorno o por `secrets`:

```bash
set EE_PROJECT=tu-proyecto        # Windows
export EE_PROJECT=tu-proyecto     # Linux/macOS
```

o en `.streamlit/secrets.toml`:

```toml
EE_PROJECT = "tu-proyecto"
# En un servidor sin navegador, cuenta de servicio:
# EE_SERVICE_ACCOUNT_JSON = '{"type": "service_account", ...}'
```

El clima **no** requiere ninguna clave: Open-Meteo es abierto.

Si Earth Engine no está disponible, la app entra sola en modo demostración con
datos sintéticos y lo avisa en pantalla y en el PDF.

---

## Uso

```bash
streamlit run app.py
```

1. **Lotes** — dibujá el perímetro sobre el mapa satelital o importá un
   archivo. Cargá cultivo, fecha de siembra y textura de suelo: de ahí salen
   los grados-día, el balance hídrico y la alineación histórica.
2. **Panel** — el estado del lote en una pantalla.
3. **Vegetación / Clima / Ambientes / Historia** — el detalle de cada eje.
4. **Informe** — PDF, Excel y paquetes geoespaciales.

---

## Compartir la app con otras personas

La app tiene tres modos de acceso y elige solo según lo que esté configurado.

| Configurado | Modo | Qué pasa |
|---|---|---|
| Nada | **Local** | No pide nada. Todo queda bajo la identidad `local`. Es el modo de trabajo en tu máquina. |
| `AGROLENS_PASSWORD` | **Clave compartida** | Una única clave. Todos ven y editan los mismos lotes. |
| Sección `[auth]` | **Cuentas de Google** | Cada persona entra con su cuenta y ve **sólo sus lotes**, más los que le compartan. |

### Activar el login con Google

1. En [console.cloud.google.com](https://console.cloud.google.com) → **API y
   servicios → Pantalla de consentimiento OAuth**: tipo *Externo*, nombre de la
   app y tu email de contacto.
2. **Credenciales → Crear credenciales → ID de cliente de OAuth → Aplicación
   web**. En *URI de redireccionamiento autorizados* poné la URL de tu app con
   `/oauth2callback` al final. Tiene que coincidir **exactamente**, incluido
   http/https y el puerto.
3. Copiá el ID y el secreto a `.streamlit/secrets.toml`:

```toml
[auth]
redirect_uri = "https://tu-app.streamlit.app/oauth2callback"
cookie_secret = "una-cadena-larga-y-aleatoria"
client_id = "xxxxxxxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxxxxxxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

4. **Limitá quién entra.** Sin esto, cualquier persona con cuenta de Google
   puede registrarse:

```toml
AGROLENS_ALLOWED_EMAILS = "vos@ejemplo.com, cliente@campo.com.ar"
# o, para toda una empresa:
AGROLENS_ALLOWED_DOMAIN = "tuempresa.com"
```

Para probarlo en tu máquina antes de publicar, usá
`redirect_uri = "http://localhost:8501/oauth2callback"` y cargá esa misma URL
en la consola de Google.

### Cómo funciona el acceso a los lotes

- Cada lote **pertenece a la cuenta que lo creó**. Nadie más lo ve.
- El dueño puede compartirlo desde **Lotes → Compartir**, por email, en
  **sólo lectura** (analiza y descarga informes) o en **lectura y edición**
  (además cambia cultivo, fechas y geometría). Borrar es siempre exclusivo del
  dueño.
- Quitar un compartido corta el acceso en el acto.
- Los permisos se verifican en la capa de persistencia, no en la interfaz: una
  página no puede saltearlos por olvido.

Si ya venías usando la app sin login, tus lotes quedaron bajo la identidad
`local`. Al activar el login, **Ajustes** te ofrece pasarlos a tu cuenta con un
botón. No es automático a propósito: si lo fuera, el primer desconocido que
entrara a la app publicada se quedaría con ellos.

### Los usuarios no necesitan cuenta de Earth Engine

Todas las consultas satelitales las hace el servidor con **una sola credencial**:
la cuenta de servicio configurada en `EE_SERVICE_ACCOUNT_JSON`. El usuario final
no se registra en Earth Engine, no autentica contra Google Cloud y no necesita
saber que existe. Su cuenta de Google sirve únicamente para identificarlo y
separar sus lotes de los de los demás.

Dos consecuencias que sí te tocan a vos, como quien publica la app:

- **Licencia.** Earth Engine es gratuito para uso no comercial: instituciones
  académicas, organizaciones sin fines de lucro en investigación o educación, y
  periodismo. Las empresas privadas que lo usan de forma operativa necesitan una
  cuenta comercial paga, y las organizaciones sin fines de lucro no pueden
  usarlo para actividades con cobro. Si la app pasa a ser parte de un servicio
  que facturás, revisá bajo qué régimen está registrado tu proyecto antes de
  sumar clientes.
- **Cuota compartida.** Todo el uso de todos los usuarios consume el cómputo
  mensual del mismo proyecto. El caché en disco amortigua bastante — dos
  personas que miran el mismo lote la misma semana gastan una sola consulta, y
  reabrir un lote ya analizado no gasta nada — pero el techo existe y es por
  proyecto, no por usuario.

### Lo que este modelo no hace

No hay roles ni administrador: todos los usuarios son iguales y cada uno maneja
lo suyo. No hay invitaciones por email — compartís un lote y la persona lo ve la
próxima vez que entra, pero avisarle corre por tu cuenta. Y todos los lotes
viven en la misma base SQLite: sirve holgadamente para decenas de usuarios, pero
si esto crece a cientos, corresponde mover la persistencia a Postgres.

---

## Deploy

### Paso 0 — la cuenta de servicio de Earth Engine

Es el único paso obligatorio y el que más se pasa por alto: la credencial que
crea `earthengine authenticate` es **tuya y de tu máquina**, no sirve en un
servidor. Hay que crear una cuenta de servicio:

1. En la [consola de Google Cloud](https://console.cloud.google.com/iam-admin/serviceaccounts),
   dentro del proyecto que ya tenés registrado en Earth Engine: **Crear cuenta
   de servicio** (por ejemplo `agrolens@tu-proyecto.iam.gserviceaccount.com`).
2. En esa cuenta: **Claves → Agregar clave → Crear clave nueva → JSON**. Se
   descarga un archivo. No lo subas al repositorio.
3. Verificá que el proyecto esté registrado en Earth Engine y que la API esté
   habilitada: todas las cuentas de servicio del proyecto heredan el acceso.
4. Cargá el contenido del JSON, en una sola línea, en el secreto
   `EE_SERVICE_ACCOUNT_JSON`, y el id del proyecto en `EE_PROJECT`.

En Google Cloud (Cloud Run, GCE, GKE) podés saltear el archivo de clave: si le
asignás al servicio una cuenta con permiso de Earth Engine, la app la detecta
sola por *Application Default Credentials*. Es la opción más segura porque no
hay clave que rotar ni que filtrar.

El clima no necesita nada: Open-Meteo es abierto.

### Opción A — Streamlit Community Cloud (gratis, 10 minutos)

La más rápida si sólo tenés git. Necesitás una cuenta de GitHub.

```bash
git init && git add . && git commit -m "AgroLens"
git remote add origin https://github.com/TU-USUARIO/agrolens.git
git push -u origin main
```

Después, en [share.streamlit.io](https://share.streamlit.io): **New app**, elegí
el repositorio, archivo principal `app.py`, y en **Advanced settings → Secrets**
pegá:

```toml
EE_PROJECT = "tu-proyecto-ee"
EE_SERVICE_ACCOUNT_JSON = '{"type":"service_account", ... }'
AGROLENS_ALLOWED_EMAILS = "vos@ejemplo.com, cliente@campo.com.ar"

[auth]
redirect_uri = "https://tu-app.streamlit.app/oauth2callback"
cookie_secret = "una-cadena-larga-y-aleatoria"
client_id = "xxxxxxxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxxxxxxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Tres cosas que conviene saber antes de elegir este camino:

- **El disco es efímero.** Cuando la app duerme por inactividad o se
  redespliega, se pierden los lotes guardados y el caché. Usá
  **Ajustes → Exportar todos los lotes** para bajar la copia y **Restaurar
  lotes** para volver a subirla. El primer análisis de cada lote vuelve a
  tardar porque el caché arranca vacío.
- **La memoria es acotada.** Streamlit no publica la cifra; en la práctica
  ronda 1 GB, y descargar el ráster de un lote grande es lo que más consume. Si
  ves "app has gone over its resource limits", bajá la resolución subiendo
  `s2_scale_m` en `agrolens/config.py`.
- **Es público salvo que pongas clave.** Definí `AGROLENS_PASSWORD`.

### Opción B — Docker en un servidor propio (persistente)

Lo que corresponde si vas a mostrarle esto a clientes. Un VPS de 2 GB alcanza y
sale unos pocos dólares por mes.

```bash
cp .env.example .env   # completar EE_PROJECT, EE_SERVICE_ACCOUNT_JSON, AGROLENS_PASSWORD
docker compose up -d --build
```

Queda en `http://tu-servidor:8501`. El volumen `agrolens-data` conserva lotes,
caché y exportaciones entre redeploys. Para HTTPS y dominio propio, poné Caddy o
Nginx adelante — con Caddy son dos líneas y el certificado es automático.

### Opción C — Google Cloud Run (escala a cero, sin clave que rotar)

Natural si ya estás en Google Cloud, porque Earth Engine vive ahí:

```bash
gcloud run deploy agrolens --source . --region southamerica-east1 \
  --service-account agrolens@tu-proyecto.iam.gserviceaccount.com \
  --set-env-vars EE_PROJECT=tu-proyecto --memory 2Gi --timeout 600
```

Sin archivo de clave: la identidad la pone el servicio. Ojo con dos cosas: el
disco también es efímero (montá un bucket con Cloud Storage FUSE si querés
persistir `/data`), y al escalar a cero la primera visita después de un rato
paga el arranque en frío.

### Antes de publicar

- [ ] `AGROLENS_PASSWORD` definida, o asumido que la URL es pública.
- [ ] El JSON de la cuenta de servicio está en secretos, **nunca** en el repo
      (`.gitignore` ya excluye `.env`, `secrets.toml` y `*-service-account*.json`).
- [ ] `pytest -q` en verde.
- [ ] Probada la pestaña **Ajustes → Probar conexión**: tiene que decir con qué
      credencial se conectó.
- [ ] Decidido qué pasa con los lotes si el disco es efímero.

**Sobre los datos de varios usuarios:** la app guarda todos los lotes en una
misma base, sin cuentas ni roles. Con la clave de acceso alcanza para un equipo
o un grupo de clientes que se conocen entre sí; si necesitás que cada cliente
vea sólo lo suyo, hay que ponerle un sistema de usuarios real o desplegar una
instancia por cliente.

---

## Arquitectura

```
app.py                    navegación
views/                    una página por eje de análisis (sólo dibujan)
agrolens/
├── config.py             rutas, umbrales, paleta validada
├── crops.py              parámetros agronómicos por cultivo
├── indices.py            catálogo de índices, con fórmula única por índice
├── models.py             Lote, Alert, AnalysisConfig, SceneInfo…
├── geo.py                geometrías, superficies, importación de archivos
├── cache.py              caché en disco persistente
├── storage.py            SQLite de lotes e informes
├── pipeline.py           orquestador: devuelve un AnalysisResult
├── sources/              gee.py · weather.py · demo.py
├── analytics/            timeseries · phenology · agronomy · zones · anomaly · alerts
├── viz/                  theme · charts · maps
├── report/               pdf · exports
└── ui/                   componentes de interfaz
```

Tres decisiones que sostienen el resto:

- **La interfaz no calcula.** Pide un `AnalysisResult` y lo dibuja. El PDF usa
  el mismo objeto, así que el informe no puede contradecir a la pantalla.
- **Cada índice se define una sola vez.** La fórmula se evalúa con numpy o con
  Earth Engine inyectando el motor correspondiente, así el mapa y la serie
  nunca divergen.
- **Degradación explícita.** Si falla el clima siguen los índices; si falla el
  ráster siguen las series; si no hay Earth Engine entra el modo demostración.
  Cada degradación queda registrada en `result.avisos` y se muestra al usuario.

---

## Método

**Satelital.** Copernicus Sentinel-2 L2A armonizado, 10 m. El enmascarado
combina la probabilidad de s2cloudless (umbral 40 %) con las clases de nube,
sombra, cirrus y nieve de la banda SCL, y dilata el resultado 60 m: los bordes
de nube son el principal sesgo en lotes chicos. Se descartan las fechas con
menos del 60 % de píxeles válidos dentro del lote.

**Curva.** Las caídas aisladas se filtran sólo hacia abajo — una subida brusca
del índice suele ser real (emergencia, riego, un corte), una bajada de un día
que se recupera casi nunca lo es. Después va a grilla diaria y se suaviza con
Savitzky-Golay, que conserva el pico.

**Fenología.** Umbral dinámico al 35 % de la amplitud de la propia campaña
(Jönsson & Eklundh), no un valor fijo de NDVI: funciona igual en trigo que en
soja.

**Clima.** Reanálisis ERA5 vía Open-Meteo, con pronóstico de 16 días y normales
1991–2020 calculadas en el mismo punto. Se eligió ERA5 antes que la
precipitación satelital tipo GPM porque a escala de lote tiene menos ruido.

**Balance hídrico.** FAO-56 de un reservorio, con dos diferencias respecto del
manual: el Kc se deriva del NDVI observado en vez de una curva teórica — así el
balance "ve" una implantación fallida — y el Ks diario se pondera por la
ventana crítica de cada cultivo.

**Zonas.** k-medias sobre el compuesto del índice, con filtro de mediana previo
y reetiquetado por media: la zona 1 es siempre la de menor vigor, en todas las
corridas. La estabilidad cruza la media plurianual con su variabilidad.

**Gráficos.** Paleta validada para daltonismo, un solo eje Y por panel (nunca
ejes dobles), color por entidad y no por ranking, leyenda siempre que haya dos
o más series y tabla alternativa en cada gráfico.

---

## Límites

Son parte del método, no defectos a disimular:

- Los índices espectrales describen el **canopeo**. No miden rendimiento,
  nutrición ni plagas: orientan el recorrido a campo, no lo reemplazan.
- La **estimación de rendimiento** es un modelo simple sobre la integral del
  índice y el balance hídrico. Se publica siempre con su rango y su nivel de
  confianza. No reemplaza un aforo.
- El **balance hídrico** no contempla napa freática ni riego, y arranca de un
  supuesto de perfil al 70 %. Sirve para comparar situaciones, no como lectura
  absoluta de milímetros.
- Sentinel-2 pasa cada 5 días: con nubes, el intervalo real puede irse a tres
  semanas. La página **Vegetación → Calidad del dato** muestra exactamente
  cuántas observaciones hubo y cuál fue el hueco más largo.
- El **modo demostración** produce datos verosímiles pero falsos. Está
  señalizado en pantalla, en el Excel y en el PDF.

---

## Pruebas

```bash
pytest -q
```

41 pruebas que cubren geometría, índices, series, fenología, agronomía, zonas,
prescripción, comparación histórica, alertas y el pipeline completo. No usan
red: el generador sintético es determinista por lote.

---

## Licencia y datos

Código propio. Los datos provienen de Copernicus Sentinel-2 (ESA, licencia
abierta), Google Earth Engine (uso no comercial gratuito, sujeto a sus
términos) y Open-Meteo (CC BY 4.0). Si publicás resultados, citá las tres.
