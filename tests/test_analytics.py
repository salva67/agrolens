"""Pruebas de la capa analítica.

No tocan la red: usan el generador sintético, que es determinista por lote.
Ejecutar con:  pytest -q
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agrolens.analytics import agronomy, anomaly, phenology, timeseries, zones  # noqa: E402
from agrolens.analytics.alerts import evaluate  # noqa: E402
from agrolens.crops import get_crop  # noqa: E402
from agrolens.geo import area_ha, centroid_latlon, validate  # noqa: E402
from agrolens.indices import INDICES, NumpyOps  # noqa: E402
from agrolens.sources import demo  # noqa: E402

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-62.95, -36.75], [-62.94, -36.75], [-62.94, -36.74],
                     [-62.95, -36.74], [-62.95, -36.75]]],
}
SOWING = date(2025, 11, 15)
START, END = SOWING - timedelta(days=20), SOWING + timedelta(days=150)


# --------------------------------------------------------------------------
# Geometría
# --------------------------------------------------------------------------
def test_area_de_un_cuadrado_conocido():
    # ~0,01° de lon x 0,01° de lat cerca de −36,75° ≈ 89 ha
    ha = area_ha(SQUARE)
    assert 80 < ha < 100


def test_validate_rechaza_lote_minusculo():
    from agrolens.geo import GeoError

    tiny = {"type": "Polygon", "coordinates": [[[-62.9500, -36.7500], [-62.9499, -36.7500],
                                                [-62.9499, -36.7499], [-62.9500, -36.7499],
                                                [-62.9500, -36.7500]]]}
    with pytest.raises(GeoError):
        validate(tiny)


def test_centroide_dentro_del_lote():
    lat, lon = centroid_latlon(SQUARE)
    assert -36.75 < lat < -36.74 and -62.95 < lon < -62.94


def test_importa_geojson_feature_collection():
    import json

    from agrolens.geo import read_uploaded

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nombre": "L1"}, "geometry": SQUARE}]}
    geom = read_uploaded("lote.geojson", json.dumps(fc).encode())
    assert geom.geom_type in ("Polygon", "MultiPolygon")
    assert 80 < area_ha(geom) < 100


def test_importa_kml():
    from agrolens.geo import read_uploaded

    coords = " ".join(f"{x},{y},0" for x, y in SQUARE["coordinates"][0])
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Lote</name>
<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""
    geom = read_uploaded("lote.kml", kml.encode())
    assert 80 < area_ha(geom) < 100


def test_importa_kmz():
    import io
    import zipfile

    from agrolens.geo import read_uploaded

    coords = " ".join(f"{x},{y},0" for x, y in SQUARE["coordinates"][0])
    kml = (f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2">'
           f"<Document><Placemark><Polygon><outerBoundaryIs><LinearRing>"
           f"<coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs>"
           f"</Polygon></Placemark></Document></kml>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml)
    geom = read_uploaded("lote.kmz", buf.getvalue())
    assert 80 < area_ha(geom) < 100


def test_formato_no_soportado_da_mensaje_claro():
    from agrolens.geo import GeoError, read_uploaded

    with pytest.raises(GeoError, match="Formato no soportado"):
        read_uploaded("lote.dwg", b"lo que sea")


ANA, BETO = "ana@ejemplo.com", "beto@ejemplo.com"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Base aislada por prueba."""
    import agrolens.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", tmp_path / "test.sqlite")
    return storage_mod


def _lote(nombre="L1", **kw):
    from agrolens.models import Lote

    return Lote(name=nombre, farm="Campo", geometry=SQUARE, crop="maiz",
                sowing_date=SOWING, **kw)


def test_guardar_y_recuperar_lote(store):
    """La persistencia recalcula superficie y centroide, y asigna dueño."""
    lote = store.save_lote(_lote(), ANA)
    assert lote.owner == ANA

    recuperado = store.get_lote(lote.id, ANA)
    assert recuperado is not None
    assert recuperado.name == "L1" and recuperado.crop == "maiz"
    assert recuperado.sowing_date == SOWING
    assert 80 < recuperado.area_ha < 100
    assert recuperado.access == store.DUEÑO
    assert len(store.list_lotes(ANA)) == 1

    store.delete_lote(lote.id, ANA)
    assert store.list_lotes(ANA) == []


def test_un_usuario_no_ve_los_lotes_de_otro(store):
    lote = store.save_lote(_lote("De Ana"), ANA)
    assert store.list_lotes(BETO) == []
    assert store.get_lote(lote.id, BETO) is None
    assert store.access_level(lote.id, BETO) is None


def test_un_usuario_no_puede_borrar_ni_pisar_el_lote_de_otro(store):
    lote = store.save_lote(_lote("De Ana"), ANA)

    with pytest.raises(store.AccessDenied):
        store.delete_lote(lote.id, BETO)

    intruso = _lote("Secuestrado")
    intruso.id = lote.id  # mismo id, otra cuenta
    with pytest.raises(store.AccessDenied):
        store.save_lote(intruso, BETO)

    assert store.get_lote(lote.id, ANA).name == "De Ana"


def test_compartir_en_lectura(store):
    lote = store.save_lote(_lote("Compartido"), ANA)
    store.share_lote(lote.id, ANA, BETO, store.LECTURA)

    visto = store.get_lote(lote.id, BETO)
    assert visto is not None and visto.access == store.LECTURA
    assert visto.owner == ANA
    assert [l.id for l in store.list_lotes(BETO)] == [lote.id]

    # lectura no habilita a escribir ni a borrar
    visto.name = "Renombrado"
    with pytest.raises(store.AccessDenied):
        store.save_lote(visto, BETO)
    with pytest.raises(store.AccessDenied):
        store.delete_lote(lote.id, BETO)
    assert store.get_lote(lote.id, ANA).name == "Compartido"


def test_compartir_en_edicion_permite_grabar_pero_no_borrar(store):
    lote = store.save_lote(_lote("Editable"), ANA)
    store.share_lote(lote.id, ANA, BETO, store.EDICION)

    visto = store.get_lote(lote.id, BETO)
    visto.name = "Editado por Beto"
    store.save_lote(visto, BETO)

    assert store.get_lote(lote.id, ANA).name == "Editado por Beto"
    assert store.get_lote(lote.id, ANA).owner == ANA  # no cambia de dueño
    with pytest.raises(store.AccessDenied):
        store.delete_lote(lote.id, BETO)


def test_dejar_de_compartir_corta_el_acceso(store):
    lote = store.save_lote(_lote(), ANA)
    store.share_lote(lote.id, ANA, BETO)
    assert store.get_lote(lote.id, BETO) is not None

    store.unshare_lote(lote.id, ANA, BETO)
    assert store.get_lote(lote.id, BETO) is None
    assert store.list_lotes(BETO) == []


def test_solo_el_dueno_administra_los_compartidos(store):
    lote = store.save_lote(_lote(), ANA)
    store.share_lote(lote.id, ANA, BETO, store.EDICION)

    with pytest.raises(store.AccessDenied):
        store.list_shares(lote.id, BETO)
    with pytest.raises(store.AccessDenied):
        store.share_lote(lote.id, BETO, "carla@ejemplo.com")

    assert [s["email"] for s in store.list_shares(lote.id, ANA)] == [BETO]


def test_compartir_valida_el_email(store):
    lote = store.save_lote(_lote(), ANA)
    with pytest.raises(ValueError):
        store.share_lote(lote.id, ANA, "no-es-un-email")
    with pytest.raises(ValueError):
        store.share_lote(lote.id, ANA, ANA)  # a uno mismo
    with pytest.raises(ValueError):
        store.share_lote(lote.id, ANA, BETO, "administrador")


def test_base_sin_sesion_no_devuelve_nada(store):
    store.save_lote(_lote(), ANA)
    assert store.list_lotes("") == []
    with pytest.raises(store.AccessDenied):
        store.save_lote(_lote("Anonimo"), "")


def test_migracion_desde_base_de_un_solo_usuario(store, tmp_path):
    """Una base creada antes del modelo multiusuario no puede perder lotes."""
    import json
    import sqlite3
    from datetime import datetime

    con = sqlite3.connect(store.DB_PATH)
    con.execute("CREATE TABLE lotes (id TEXT PRIMARY KEY, name TEXT NOT NULL, farm TEXT, "
                "crop TEXT, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
    viejo = _lote("Lote viejo")
    con.execute("INSERT INTO lotes VALUES (?,?,?,?,?,?)",
                (viejo.id, viejo.name, viejo.farm, viejo.crop,
                 json.dumps(viejo.to_dict(), default=str), datetime.now().isoformat()))
    con.commit()
    con.close()

    # Antes de adoptarlos, un lote sin dueño no es de nadie
    assert store.list_lotes(ANA) == []
    assert store.claim_orphans(ANA) == 1

    adoptados = store.list_lotes(ANA)
    assert len(adoptados) == 1 and adoptados[0].name == "Lote viejo"
    assert store.claim_orphans(BETO) == 0  # ya tienen dueño


def test_los_lotes_locales_no_se_adoptan_solos(store):
    """Al publicar la app, el primer usuario que entre no puede quedarse con
    los lotes cargados en la máquina de desarrollo: hace falta un acto explícito."""
    store.save_lote(_lote("Cargado en local"), "local")

    assert store.claim_orphans(ANA) == 0  # tienen dueño ('local'), no son huérfanos
    assert store.list_lotes(ANA) == []
    assert store.count_local_lotes() == 1

    assert store.claim_local(ANA) == 1
    assert [l.name for l in store.list_lotes(ANA)] == ["Cargado en local"]
    assert store.count_local_lotes() == 0
    assert store.claim_local(BETO) == 0


def test_exportar_e_importar_lotes_conserva_los_datos(store):
    """La copia de seguridad tiene que poder restaurarse tal cual: es la única
    red de contención cuando la app corre sobre un disco efímero."""
    import json

    from agrolens.geo import to_feature_collection, to_geojson_feature
    from agrolens.models import Lote

    original = Lote(name="L1", farm="Campo", geometry=SQUARE, crop="trigo",
                    sowing_date=SOWING, soil_texture="Franco limoso", yield_target_tha=4.2,
                    notes="con acento: ñ")
    store.save_lote(original, ANA)

    fc = to_feature_collection([
        to_geojson_feature(l.geometry, {k: v for k, v in l.to_dict().items()
                                        if k not in ("geometry", "access")})
        for l in store.list_lotes(ANA)
    ])
    payload = json.loads(json.dumps(fc, default=str))

    store.delete_lote(original.id, ANA)
    assert store.list_lotes(ANA) == []

    for f in payload["features"]:
        props = dict(f["properties"])
        props["geometry"] = f["geometry"]
        props.pop("owner", None)
        store.save_lote(Lote.from_dict(props), ANA)

    restaurado = store.get_lote(original.id, ANA)
    assert restaurado is not None
    assert restaurado.name == "L1" and restaurado.crop == "trigo"
    assert restaurado.sowing_date == SOWING
    assert restaurado.soil_texture == "Franco limoso"
    assert restaurado.yield_target_tha == 4.2
    assert restaurado.notes == "con acento: ñ"


# --------------------------------------------------------------------------
# Índices
# --------------------------------------------------------------------------
def test_ndvi_de_vegetacion_y_de_suelo():
    ops = NumpyOps()
    veg = {"nir": np.array([0.40]), "red": np.array([0.05]), "green": np.array([0.08]),
           "blue": np.array([0.04]), "swir1": np.array([0.20]), "swir2": np.array([0.12]),
           "re1": np.array([0.15]), "nir8a": np.array([0.42])}
    suelo = {k: np.array([0.20]) for k in veg}
    assert INDICES["NDVI"].compute(veg, ops)[0] > 0.7
    assert abs(INDICES["NDVI"].compute(suelo, ops)[0]) < 0.01


def test_todos_los_indices_devuelven_valores_finitos():
    ops = NumpyOps()
    bandas = {"blue": np.array([0.04]), "green": np.array([0.08]), "red": np.array([0.05]),
              "re1": np.array([0.15]), "re2": np.array([0.25]), "re3": np.array([0.32]),
              "nir": np.array([0.40]), "nir8a": np.array([0.42]),
              "swir1": np.array([0.20]), "swir2": np.array([0.12])}
    for key, idx in INDICES.items():
        val = idx.compute(bandas, ops)
        assert np.isfinite(val).all(), f"{key} devolvió un valor no finito"


# --------------------------------------------------------------------------
# Series temporales y fenología
# --------------------------------------------------------------------------
def test_curva_diaria_es_continua():
    serie = demo.index_series(SQUARE, START, END, "NDVI", "soja", SOWING)
    curva = timeseries.build_curve(serie, "mean", 21, START, END)
    assert len(curva) == (END - START).days + 1
    assert curva["smooth"].notna().all()


def test_filtro_descarta_caida_por_nube_pero_no_una_subida():
    import pandas as pd

    fechas = [START + timedelta(days=5 * i) for i in range(12)]
    valores = [0.5] * 12
    valores[5] = 0.10  # nube
    df = pd.DataFrame({"date": fechas, "mean": valores})
    limpio = timeseries.drop_low_outliers(df, "mean")
    assert 0.10 not in list(limpio["mean"])

    valores2 = [0.5] * 12
    valores2[5] = 0.85  # rebrote real
    df2 = pd.DataFrame({"date": fechas, "mean": valores2})
    assert 0.85 in list(timeseries.drop_low_outliers(df2, "mean")["mean"])


def test_fenologia_encuentra_el_pico_dentro_del_ciclo():
    serie = demo.index_series(SQUARE, START, END, "NDVI", "soja", SOWING)
    curva = timeseries.build_curve(serie, "mean", 21, START, END)
    ph = phenology.extract(curva)
    assert ph.pos is not None
    dias = (ph.pos - SOWING).days
    assert 40 < dias < 120, f"pico en el día {dias}"
    assert ph.sos is None or ph.sos < ph.pos
    assert ph.eos is None or ph.eos > ph.pos


def test_integral_crece_con_la_biomasa():
    serie = demo.index_series(SQUARE, START, END, "NDVI", "soja", SOWING)
    curva = timeseries.build_curve(serie, "mean", 21, START, END)
    total = timeseries.integral(curva)
    parcial = timeseries.integral(curva, end=SOWING + timedelta(days=60))
    assert 0 < parcial < total


# --------------------------------------------------------------------------
# Agronomía
# --------------------------------------------------------------------------
def test_grados_dia_no_acumulan_antes_de_la_siembra():
    wx = demo.weather(-36.75, -62.95, START, END)
    gdd = agronomy.growing_degree_days(wx, get_crop("soja"), SOWING)
    previos = gdd[gdd["date"] < SOWING]["gdd"]
    assert (previos == 0).all()
    assert gdd["gdd_acum"].iloc[-1] > 800


def test_balance_hidrico_respeta_la_capacidad_del_perfil():
    wx = demo.weather(-36.75, -62.95, START, END)
    wb = agronomy.water_balance(wx, get_crop("soja"), SOWING, awc_mm=150)
    assert wb["agua_util_mm"].max() <= 150 + 1e-6
    assert wb["agua_util_mm"].min() >= -1e-6
    assert (wb["ks"] <= 1.0 + 1e-9).all() and (wb["ks"] >= 0).all()
    assert (wb["eta_mm"] <= wb["etc_mm"] + 1e-9).all()


def test_estres_severo_cuando_no_llueve():
    import pandas as pd

    dias = pd.date_range(SOWING, periods=120, freq="D")
    seco = pd.DataFrame({
        "date": [d.date() for d in dias], "tmax": 32.0, "tmin": 16.0, "tmean": 24.0,
        "precip_mm": 0.0, "et0_mm": 6.0, "rad_mj": 25.0, "wind_kmh": 12.0,
        "source": "observado",
    })
    wb = agronomy.water_balance(seco, get_crop("maiz"), SOWING, awc_mm=120)
    resumen = agronomy.water_stress_summary(wb, get_crop("maiz"), SOWING)
    assert resumen["dias_estres"] > 40
    assert resumen["satisfaccion_hidrica"] < 0.7
    assert resumen["penalidad_rinde"] > 0.2


def test_rachas_secas_detectan_el_periodo_sin_lluvia():
    wx = demo.weather(-36.75, -62.95, START, END)
    wx.loc[20:45, "precip_mm"] = 0.0
    rachas = agronomy.dry_spells(wx, min_days=10)
    assert not rachas.empty
    assert rachas["dias"].max() >= 20


def test_racha_seca_no_atraviesa_un_hueco_de_la_serie():
    """Un salto en las fechas corta la racha: si no, dos semanas secas se
    informarían como tres meses."""
    import pandas as pd

    fechas = ([date(2026, 3, 1) + timedelta(days=i) for i in range(14)]
              + [date(2026, 6, 1) + timedelta(days=i) for i in range(5)])
    wx = pd.DataFrame({"date": fechas, "precip_mm": 0.0, "et0_mm": 4.0,
                       "tmax": 28.0, "tmin": 12.0, "source": "observado"})
    rachas = agronomy.dry_spells(wx, min_days=10)
    assert len(rachas) == 1
    assert rachas["dias"].iloc[0] == 14
    assert rachas["fin"].iloc[0] == date(2026, 3, 14)


def test_uniformidad_no_se_calcula_con_cobertura_baja():
    """Con la media del índice baja, el CV se dispara sin que el lote sea
    heterogéneo: en ese caso no hay dato, no un cero."""
    import math

    from agrolens.models import uniformity_score

    assert math.isnan(uniformity_score(0.18, 0.09))  # suelo desnudo
    assert uniformity_score(0.80, 0.04) > 85  # canopeo cerrado y parejo
    assert uniformity_score(0.60, 0.24) < 20  # canopeo cerrado y desparejo


def test_kc_desde_ndvi_crece_con_la_cobertura():
    bajo = agronomy.kc_from_ndvi(0.20)
    alto = agronomy.kc_from_ndvi(0.85)
    assert bajo < alto
    assert 0.15 < float(bajo) < 0.45 and 1.0 < float(alto) <= 1.2


def test_rendimiento_penaliza_el_estres():
    crop = get_crop("soja")
    sin_estres = agronomy.yield_estimate(crop.indvi_ref, crop, 0.0, 100)
    con_estres = agronomy.yield_estimate(crop.indvi_ref, crop, 0.3, 100)
    assert sin_estres["estimado_tha"] > con_estres["estimado_tha"]
    assert sin_estres["rango_tha"][0] < sin_estres["estimado_tha"] < sin_estres["rango_tha"][1]


# --------------------------------------------------------------------------
# Zonas
# --------------------------------------------------------------------------
def test_zonas_quedan_ordenadas_de_menor_a_mayor():
    raster = demo.raster(SQUARE, "NDVI", 0.65)
    z = zones.management_zones(raster, 3)
    medias = [s.mean for s in z["stats"]]
    assert medias == sorted(medias), "la zona 1 debe ser siempre la de menor índice"
    assert abs(sum(s.pct for s in z["stats"]) - 100) < 0.5


def test_prescripcion_mantiene_la_dosis_media():
    raster = demo.raster(SQUARE, "NDVI", 0.65)
    z = zones.management_zones(raster, 3)
    presc = zones.prescription(z["stats"], base_dose=120, strategy="compensar", spread_pct=25)
    col = [c for c in presc.columns if c.startswith("dosis_")][0]
    ponderada = (presc[col] * presc["superficie_ha"]).sum() / presc["superficie_ha"].sum()
    assert abs(ponderada - 120) < 1.0


def test_estrategia_compensar_da_mas_dosis_a_la_zona_pobre():
    raster = demo.raster(SQUARE, "NDVI", 0.65)
    z = zones.management_zones(raster, 3)
    presc = zones.prescription(z["stats"], 120, "compensar")
    col = [c for c in presc.columns if c.startswith("dosis_")][0]
    assert presc[col].iloc[0] > presc[col].iloc[-1]

    potenciar = zones.prescription(z["stats"], 120, "potenciar")
    assert potenciar[col].iloc[0] < potenciar[col].iloc[-1]


def test_estabilidad_requiere_varias_campanas():
    with pytest.raises(ValueError):
        zones.stability_zones([demo.raster(SQUARE, "NDVI", 0.6)])


# --------------------------------------------------------------------------
# Historia y alertas
# --------------------------------------------------------------------------
def test_banda_historica_y_ranking():
    def fetch(geom, start, end, index_key):
        return demo.index_series(geom, start, end, index_key, "soja",
                                 start + timedelta(days=20))

    hist = anomaly.build_history(fetch, SQUARE, SOWING, 145, "NDVI", n_years=4)
    assert not hist.empty
    banda = anomaly.envelope(hist)
    assert (banda["p10"] <= banda["p50"]).all()
    assert (banda["p50"] <= banda["p90"]).all()

    serie = demo.index_series(SQUARE, START, END, "NDVI", "soja", SOWING)
    curva = timeseries.build_curve(serie, "mean", 21, START, END)
    rank = anomaly.rank_current(curva, SOWING, hist)
    assert not rank.empty
    assert rank["percentil"].between(0, 100).all()


def test_alertas_detectan_caida_fuerte():
    import pandas as pd

    curva = pd.DataFrame({
        "date": [START + timedelta(days=i) for i in range(30)],
        "smooth": np.linspace(0.75, 0.45, 30),
    })
    tendencia = timeseries.trend(curva)
    alertas = evaluate(crop=get_crop("soja"), curve=curva, trend=tendencia)
    codigos = {a.code for a in alertas}
    assert "VEG_CAIDA" in codigos
    assert all(a.recommendation for a in alertas), "toda alerta debe decir qué hacer"


def test_alertas_ordenadas_por_severidad():
    from agrolens.models import Alert

    lista = [
        Alert(code="a", severity="info", title="A", detail=""),
        Alert(code="b", severity="critical", title="B", detail=""),
        Alert(code="c", severity="warning", title="C", detail=""),
    ]
    ordenadas = sorted(lista, key=lambda a: a.rank)
    assert [a.code for a in ordenadas] == ["b", "c", "a"]


# --------------------------------------------------------------------------
# Tormentas
# --------------------------------------------------------------------------
def _wx_dias(n=30, inicio=SOWING, **overrides):
    import pandas as pd

    d = pd.DataFrame({
        "date": [inicio + timedelta(days=i) for i in range(n)],
        "tmax": 28.0, "tmin": 15.0, "tmean": 21.0, "precip_mm": 0.0, "et0_mm": 4.0,
        "rad_mj": 20.0, "wind_kmh": 15.0, "gust_kmh": 25.0, "wmo": 1,
        "precip_horas": 0.0, "source": "observado",
    })
    for k, v in overrides.items():
        d[k] = v
    return d


def test_dia_calmo_no_genera_evento():
    from agrolens.analytics import storms

    assert storms.storm_days(_wx_dias()).empty


def test_detecta_granizo_por_codigo_wmo():
    from agrolens.analytics import storms

    wx = _wx_dias()
    wx.loc[10, ["wmo", "precip_mm", "gust_kmh", "precip_horas"]] = [99, 35.0, 95.0, 2.0]
    ev = storms.storm_days(wx)
    assert len(ev) == 1
    assert bool(ev["granizo"].iloc[0]) is True
    assert ev["severidad"].iloc[0] > 70, "granizo fuerte con ráfaga debe puntuar alto"
    assert "granizo" in ev["tipo"].iloc[0].lower()


def test_rafaga_fuerte_sola_genera_evento_sin_granizo():
    from agrolens.analytics import storms

    wx = _wx_dias()
    wx.loc[5, "gust_kmh"] = 92.0
    ev = storms.storm_days(wx)
    assert len(ev) == 1
    assert bool(ev["granizo"].iloc[0]) is False
    assert ev["rafaga_kmh"].iloc[0] == 92.0


def test_severidad_ordena_los_eventos():
    from agrolens.analytics import storms

    wx = _wx_dias()
    wx.loc[3, "gust_kmh"] = 65.0                                     # leve
    wx.loc[8, ["wmo", "gust_kmh"]] = [96, 85.0]                      # granizo + viento
    wx.loc[15, ["wmo", "gust_kmh"]] = [99, 110.0]                    # lo peor
    ev = storms.storm_days(wx).sort_values("date").reset_index(drop=True)
    assert list(ev["severidad"]) == sorted(ev["severidad"]), "la severidad debe crecer"


def _serie(fechas_valores):
    import pandas as pd

    return pd.DataFrame([
        {"date": f, "mean": v, "std": 0.05, "p10": v - 0.05, "p90": v + 0.05,
         "valid_fraction": 1.0, "cloud_scene_pct": 0.0, "scene_id": "X",
         "median": v, "min": v - 0.1, "max": v + 0.1}
        for f, v in fechas_valores
    ])


def test_detecta_dano_cuando_la_caida_coincide_con_granizo():
    from agrolens.analytics import storms

    wx = _wx_dias(n=60)
    wx.loc[40, ["wmo", "gust_kmh", "precip_mm", "precip_horas"]] = [99, 100.0, 40.0, 2.0]
    ev = storms.storm_days(wx)

    serie = _serie([(SOWING + timedelta(days=36), 0.78),
                    (SOWING + timedelta(days=44), 0.55)])
    dano = storms.detect_damage(serie, ev, get_crop("soja"), SOWING)
    assert len(dano) == 1
    fila = dano.iloc[0]
    assert fila["caida"] == pytest.approx(0.23, abs=0.01)
    assert bool(fila["granizo"]) is True  # pandas devuelve np.bool_, no bool
    assert fila["confianza"] == "alta"


def test_una_caida_sin_tormenta_no_es_dano():
    from agrolens.analytics import storms

    ev = storms.storm_days(_wx_dias(n=60))  # sin eventos
    serie = _serie([(SOWING + timedelta(days=36), 0.78),
                    (SOWING + timedelta(days=44), 0.55)])
    assert storms.detect_damage(serie, ev, get_crop("soja"), SOWING).empty


def test_la_caida_de_fin_de_ciclo_no_se_reporta_como_dano():
    """Perder verde cerca de la cosecha es lo esperable: si no se descarta,
    el informe se llena de falsos positivos todos los años."""
    from agrolens.analytics import storms

    crop = get_crop("soja")
    tarde = SOWING + timedelta(days=int(crop.cycle_days * 0.95))
    wx = _wx_dias(n=200)
    idx = (tarde - SOWING).days
    wx.loc[idx, ["wmo", "gust_kmh"]] = [99, 100.0]
    ev = storms.storm_days(wx)

    serie = _serie([(tarde - timedelta(days=5), 0.60), (tarde + timedelta(days=3), 0.30)])
    assert storms.detect_damage(serie, ev, crop, SOWING).empty


def test_una_ventana_muy_larga_entre_imagenes_baja_la_confianza():
    from agrolens.analytics import storms

    wx = _wx_dias(n=60)
    wx.loc[40, ["wmo", "gust_kmh"]] = [95, 62.0]  # tormenta modesta
    ev = storms.storm_days(wx)
    serie = _serie([(SOWING + timedelta(days=32), 0.72),
                    (SOWING + timedelta(days=46), 0.61)])  # 14 días entre imágenes
    dano = storms.detect_damage(serie, ev, get_crop("soja"), SOWING)
    assert len(dano) == 1
    assert dano.iloc[0]["confianza"] in ("baja", "media")


def test_periodo_critico_se_marca_correctamente():
    from agrolens.analytics import storms

    crop = get_crop("maiz")
    lo, hi = crop.critical_window
    dentro = SOWING + timedelta(days=int(crop.cycle_days * (lo + hi) / 2))
    fuera = SOWING + timedelta(days=int(crop.cycle_days * 0.10))

    wx = _wx_dias(n=200)
    for d in (dentro, fuera):
        wx.loc[(d - SOWING).days, ["wmo", "gust_kmh"]] = [96, 90.0]
    ev = storms.critical_window_events(storms.storm_days(wx), crop, SOWING)

    assert bool(ev.loc[ev["date"] == dentro, "en_periodo_critico"].iloc[0]) is True
    assert bool(ev.loc[ev["date"] == fuera, "en_periodo_critico"].iloc[0]) is False


def test_alertas_de_tormenta_se_emiten():
    from agrolens.analytics import storms
    from agrolens.analytics.alerts import evaluate

    wx = _wx_dias(n=60)
    wx.loc[40, ["wmo", "gust_kmh", "precip_mm", "precip_horas"]] = [99, 105.0, 40.0, 2.0]
    ev = storms.critical_window_events(storms.storm_days(wx), get_crop("soja"), SOWING)
    serie = _serie([(SOWING + timedelta(days=36), 0.78),
                    (SOWING + timedelta(days=44), 0.55)])
    dano = storms.detect_damage(serie, ev, get_crop("soja"), SOWING)

    codigos = {a.code for a in evaluate(crop=get_crop("soja"), storms=ev, damage=dano)}
    assert "DANO_TORMENTA" in codigos
    assert "GRANIZO" in codigos
    assert "VIENTO" in codigos


# --------------------------------------------------------------------------
# Pipeline completo en modo demostración
# --------------------------------------------------------------------------
def test_pipeline_demo_de_punta_a_punta():
    from agrolens.models import AnalysisConfig, Lote
    from agrolens.pipeline import run

    lote = Lote(name="Prueba", geometry=SQUARE, crop="soja", sowing_date=SOWING,
                area_ha=area_ha(SQUARE), centroid=centroid_latlon(SQUARE))
    cfg = AnalysisConfig(start=START, end=END, index="NDVI")
    res = run(lote, cfg, include_history=False, demo_mode=True)

    assert res.modo_demo is True
    assert not res.series.empty and not res.curve.empty
    assert not res.clima.empty and not res.balance.empty
    assert res.zonas is not None
    score, etiqueta = res.salud()
    assert 0 <= score <= 100 and etiqueta
