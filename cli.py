"""Interfaz de linea de comandos.

Ejemplos:

    python -m granizo_riesgo.cli --lat -33.12 --lon -63.45 --fecha 2025-12-20 --proyecto ee-salvalrc

    python -m granizo_riesgo.cli --lotes lotes.csv --fecha 2025-12-20 --fecha-fin 2025-12-21 \
        --radio-km 20 --salida resultados/ --png

    python -m granizo_riesgo.cli --asset projects/ee-salvalrc/assets/mirabet1 --fecha 2025-12-20
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import ee

from . import api


def _argumentos(argv=None):
    p = argparse.ArgumentParser(
        prog="granizo_riesgo",
        description="Estima exposicion a granizo desde GOES ABI para una fecha y coordenadas.",
    )

    origen = p.add_argument_group("lotes (elegir uno)")
    origen.add_argument("--lat", type=float, help="Latitud decimal (negativa al sur).")
    origen.add_argument("--lon", type=float, help="Longitud decimal (negativa al oeste).")
    origen.add_argument("--id", default="punto", help="Identificador del punto.")
    origen.add_argument("--lotes", help="CSV con columnas id, lat, lon.")
    origen.add_argument("--asset", help="FeatureCollection de Earth Engine (usa centroides).")

    t = p.add_argument_group("ventana temporal (hora local)")
    t.add_argument("--fecha", required=True, help="Fecha inicial YYYY-MM-DD.")
    t.add_argument("--fecha-fin", help="Fecha final YYYY-MM-DD (por defecto igual a --fecha).")
    t.add_argument("--hora-inicio", default="00:00")
    t.add_argument("--hora-fin", default="24:00")
    t.add_argument("--tz-offset", type=float, default=api.TZ_OFFSET_DEFECTO,
                   help="Offset horario respecto a UTC. Argentina: -3.")

    g = p.add_argument_group("geometria y satelite")
    g.add_argument("--radio-km", type=float, default=api.RADIO_KM_DEFECTO,
                   help="Radio del area analizada alrededor de cada punto.")
    g.add_argument("--altura-nube-km", type=float, default=api.ALTURA_NUBE_KM_DEFECTO,
                   help="Altura de tope supuesta para la correccion de paralaje.")
    g.add_argument("--sin-paralaje", action="store_true",
                   help="Desactiva la correccion de paralaje (no recomendado).")
    g.add_argument("--hemisferio", choices=["east", "west"], default="east")

    s = p.add_argument_group("salida")
    s.add_argument("--salida", default=".", help="Directorio de salida.")
    s.add_argument("--png", action="store_true",
                   help="Descarga un PNG del momento de maxima severidad por lote.")
    s.add_argument("--mapa", action="store_true",
                   help="Genera mapa_granizo.html (Folium) con el raster de exposicion.")
    s.add_argument("--informe", action="store_true",
                   help="Genera un informe HTML autocontenido, listo para adjuntar a un correo.")
    s.add_argument("--establecimiento", help="Nombre del establecimiento, para el informe.")
    s.add_argument("--cliente", help="Nombre del cliente, para el informe.")
    s.add_argument("--autor", help="Quien firma el informe.")
    s.add_argument("--radio-region-km", type=float, default=120.0,
                   help="Extension del raster del mapa alrededor de los lotes.")
    s.add_argument("--umbral-mapa", type=float, default=15.0,
                   help="Exposicion minima que se dibuja en el mapa (0-100).")
    s.add_argument("--json", dest="json_out", action="store_true",
                   help="Escribe tambien resumen.json con toda la metadata.")
    s.add_argument("--proyecto", default=os.environ.get("EE_PROJECT"),
                   help="Proyecto de Google Cloud habilitado para Earth Engine.")
    s.add_argument("--sin-verificar", action="store_true",
                   help="Omite el chequeo de calibracion (ahorra una llamada).")
    return p.parse_args(argv)


def _cargar_lotes(args) -> list:
    if args.lotes:
        return api.lotes_desde_csv(args.lotes)
    if args.asset:
        return api.lotes_desde_asset(args.asset)
    if args.lat is None or args.lon is None:
        raise SystemExit("Falta indicar --lat/--lon, --lotes o --asset.")
    return [{"id": args.id, "lat": args.lat, "lon": args.lon}]


def _escribir_serie(filas: list, ruta: str) -> None:
    if not filas:
        return
    columnas = [
        "lote_id", "t_utc", "t_local", "bt_min_k", "bt_mean_k", "n_pix",
        "btd_wv_ir_max", "btd_split_min", "f235", "f225", "f215", "f205",
        "enfriamiento_k_10min", "score_escena",
    ]
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            fila = dict(fila)
            fila["t_utc"] = fila["t_utc"].isoformat()
            fila["t_local"] = fila["t_local"].isoformat()
            w.writerow(fila)


def _escribir_resumen(resumen: list, ruta: str) -> None:
    columnas = [
        "lote_id", "lat", "lon", "score", "categoria", "bt_min_c", "frac_area_lt215k",
        "btd_wv_ir_max", "enfriamiento_k_10min", "duracion_lt225k_min",
        "pico_local", "paralaje_km", "n_escenas",
    ]
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        for r in resumen:
            ind = r.get("indicadores", {})
            w.writerow(
                {
                    "lote_id": r["lote_id"],
                    "lat": r.get("lat"),
                    "lon": r.get("lon"),
                    "score": r.get("score"),
                    "categoria": r.get("categoria"),
                    "bt_min_c": ind.get("bt_min_c"),
                    "frac_area_lt215k": ind.get("frac_area_lt215k"),
                    "btd_wv_ir_max": ind.get("btd_wv_ir_max"),
                    "enfriamiento_k_10min": ind.get("enfriamiento_k_10min"),
                    "duracion_lt225k_min": ind.get("duracion_lt225k_min"),
                    "pico_local": (r.get("pico") or {}).get("t_local"),
                    "paralaje_km": r.get("paralaje_km"),
                    "n_escenas": r.get("n_escenas"),
                }
            )


def main(argv=None) -> int:
    args = _argumentos(argv)

    try:
        ee.Initialize(project=args.proyecto) if args.proyecto else ee.Initialize()
    except Exception as exc:
        print(
            "No se pudo inicializar Earth Engine ({}).\n"
            "Ejecuta 'earthengine authenticate' una vez y pasa --proyecto TU_PROYECTO "
            "(o define la variable de entorno EE_PROJECT).".format(exc),
            file=sys.stderr,
        )
        return 2

    lotes = _cargar_lotes(args)
    print("Lotes a evaluar: {}".format(len(lotes)))

    res = api.evaluar_lotes(
        lotes,
        fecha=args.fecha,
        fecha_fin=args.fecha_fin,
        hora_inicio=args.hora_inicio,
        hora_fin=args.hora_fin,
        tz_offset=args.tz_offset,
        radio_km=args.radio_km,
        altura_nube_km=args.altura_nube_km,
        corregir_paralaje_flag=not args.sin_paralaje,
        hemisferio=args.hemisferio,
        verificar=not args.sin_verificar,
    )

    meta = res["meta"]
    print("Satelite: {} ({})".format(meta["satelite"], meta["coleccion"]))
    print("Ventana local: {} -> {}".format(*meta["ventana_local"]))
    print("Escenas x lote leidas: {}\n".format(meta["n_filas"]))

    ancho = max(len(r["lote_id"]) for r in res["resumen"]) if res["resumen"] else 8
    print("{:<{w}}  {:>6}  {:<10}  {:>8}  {:>8}  {}".format(
        "LOTE", "SCORE", "CATEGORIA", "BTmin C", "OT max", "PICO (local)", w=ancho))
    print("-" * (ancho + 56))
    for r in res["resumen"]:
        ind = r.get("indicadores", {})
        print("{:<{w}}  {:>6.1f}  {:<10}  {:>8}  {:>8}  {}".format(
            r["lote_id"],
            r.get("score", 0.0),
            r.get("categoria", "-"),
            ind.get("bt_min_c", "-"),
            ind.get("btd_wv_ir_max", "-"),
            (r.get("pico") or {}).get("t_local", "-"),
            w=ancho,
        ))

    avisos = {a for r in res["resumen"] for a in r.get("advertencias", [])}
    if avisos:
        print("\nAdvertencias:")
        for a in sorted(avisos):
            print("  - {}".format(a))

    os.makedirs(args.salida, exist_ok=True)
    _escribir_serie(res["serie"], os.path.join(args.salida, "serie_temporal.csv"))
    _escribir_resumen(res["resumen"], os.path.join(args.salida, "resumen.csv"))
    print("\nEscrito: {}".format(os.path.join(args.salida, "resumen.csv")))
    print("Escrito: {}".format(os.path.join(args.salida, "serie_temporal.csv")))

    if args.json_out:
        ruta = os.path.join(args.salida, "resumen.json")
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"meta": meta, "resumen": res["resumen"]}, fh, indent=2, ensure_ascii=False)
        print("Escrito: {}".format(ruta))

    if args.png:
        for r in res["resumen"]:
            if not r.get("pico"):
                continue
            destino = os.path.join(args.salida, "pico_{}.png".format(r["lote_id"]))
            try:
                api.quicklook(res, r["lote_id"], destino)
                print("Escrito: {}".format(destino))
            except Exception as exc:
                print("No se pudo generar el PNG de {}: {}".format(r["lote_id"], exc), file=sys.stderr)

    if args.informe:
        from . import informe as informe_mod

        for r in res["resumen"]:
            png = os.path.join(args.salida, "pico_{}.png".format(r["lote_id"]))
            if not os.path.exists(png):
                try:
                    api.quicklook(res, r["lote_id"], png)
                except Exception:
                    png = None
            destino = os.path.join(
                args.salida, "informe_{}_{}.html".format(r["lote_id"], args.fecha))
            try:
                informe_mod.generar_informe(
                    res, destino, lote_id=r["lote_id"], png_pico=png,
                    establecimiento=args.establecimiento, cliente=args.cliente,
                    autor=args.autor)
                print("Escrito: {}".format(destino))
            except Exception as exc:
                print("No se pudo generar el informe de {}: {}".format(r["lote_id"], exc),
                      file=sys.stderr)

    if args.mapa:
        from . import mapa as mapa_mod

        destino = os.path.join(args.salida, "mapa_granizo.html")
        print("\nGenerando raster de exposicion (puede tardar un minuto)...")
        mapa_mod.mapa_folium(
            res,
            destino,
            radio_region_km=args.radio_region_km,
            umbral_visible=args.umbral_mapa,
        )
        print("Escrito: {}".format(destino))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
