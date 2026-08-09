"""Exposición a granizo por satélite geoestacionario (GOES ABI).

Este módulo **no calcula nada**: es un puente al paquete `granizo_riesgo`, que
ya resuelve el problema con calibración a Kelvin, corrección de paralaje y un
puntaje 0–100 validado contra el evento de Villa Carlos Paz del 8-feb-2018.
Reescribirlo acá sería peor y estaría sin validar.

El paquete es **opcional**: si no está instalado, AgroLens sigue funcionando
con la exposición derivada del registro meteorológico (ráfagas y códigos WMO),
que no necesita nada extra. La diferencia entre una y otra:

* Meteorológica — resolución de modelo, dice si *hubo* tormenta con granizo
  cerca del lote. Disponible siempre, sin costo.
* GOES — resolución de ~4 km sobre Argentina, mide la estructura vertical de
  la tormenta sobre un disco alrededor del lote y la puntúa. Mucho más
  informativa para un reclamo, pero pide el paquete y una consulta a Earth
  Engine por evento.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..cache import disk_cache

log = logging.getLogger(__name__)

RADIO_KM_DEFECTO = 20.0


class HailEngineUnavailable(RuntimeError):
    """El paquete `granizo_riesgo` no está disponible en este entorno."""


def _ensure_path() -> None:
    """Permite usar `granizo_riesgo` si está como carpeta hermana del proyecto.

    Es el caso típico en la máquina de desarrollo, donde los dos paquetes
    conviven en el mismo directorio de trabajo pero sólo uno está en el path.
    """
    import sys
    from pathlib import Path

    from ..config import ROOT_DIR

    for candidata in (ROOT_DIR, ROOT_DIR.parent):
        if (Path(candidata) / "granizo_riesgo" / "__init__.py").exists():
            if str(candidata) not in sys.path:
                sys.path.insert(0, str(candidata))
            return


def available() -> bool:
    return diagnose()["disponible"]


def diagnose() -> dict[str, Any]:
    """Por qué el motor de granizo está o no disponible.

    Devolver sólo True/False era un problema: si la carpeta estaba pero el
    import fallaba por otra razón, el mensaje culpaba a la instalación y no
    había forma de ver el motivo real desde la app publicada.
    """
    import sys
    from pathlib import Path

    from ..config import ROOT_DIR

    _ensure_path()
    candidatas = [Path(ROOT_DIR), Path(ROOT_DIR).parent]
    encontrada = next(
        (str(c / "granizo_riesgo") for c in candidatas
         if (c / "granizo_riesgo" / "__init__.py").exists()), None,
    )
    # En Linux el import distingue mayúsculas: una carpeta "Granizo_Riesgo" o
    # con espacios existe pero no se puede importar.
    similares = []
    for c in candidatas:
        try:
            similares += [d.name for d in c.iterdir()
                          if d.is_dir() and d.name != "granizo_riesgo"
                          and "granizo" in d.name.lower().replace(" ", "_")]
        except OSError:
            continue

    info: dict[str, Any] = {
        "disponible": False,
        "carpeta": encontrada,
        "buscado_en": [str(c) for c in candidatas],
        "nombres_parecidos": similares,
        "error": None,
    }
    try:
        import granizo_riesgo  # noqa: F401

        info["disponible"] = True
        info["ruta_modulo"] = getattr(granizo_riesgo, "__file__", "")
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        info["python"] = sys.version.split()[0]
    return info


def _engine():
    _ensure_path()
    try:
        import granizo_riesgo as gr

        return gr
    except Exception as exc:  # pragma: no cover - depende del entorno
        raise HailEngineUnavailable(
            "El análisis de granizo por GOES necesita el paquete `granizo_riesgo`.\n\n"
            "Si lo tenés en tu máquina, alcanza con que esté en el PYTHONPATH. "
            "Para que funcione en el servidor, copiá la carpeta `granizo_riesgo/` "
            "dentro del repositorio de AgroLens.\n\n"
            "Mientras tanto, la exposición a tormentas por ráfagas y códigos "
            "meteorológicos sigue disponible y no requiere nada."
        ) from exc


@disk_cache("granizo", ttl_hours=24 * 30)
def evaluate_event(
    lat: float,
    lon: float,
    fecha: date,
    radio_km: float = RADIO_KM_DEFECTO,
    hora_inicio: str = "00:00",
    hora_fin: str = "24:00",
) -> dict[str, Any]:
    """Puntúa la exposición a granizo de un lote en una fecha.

    Devuelve el resumen del punto (score, categoría e indicadores) más la serie
    escena por escena. El resultado se cachea un mes: una tormenta de hace tres
    semanas no va a cambiar.
    """
    gr = _engine()
    res = gr.evaluar_punto(
        lat=float(lat), lon=float(lon), fecha=str(fecha),
        hora_inicio=hora_inicio, hora_fin=hora_fin, radio_km=radio_km,
    )
    resumen = res.get("resumen_punto") or {}
    return {
        "fecha": fecha,
        "score": float(resumen.get("score") or 0.0),
        "categoria": resumen.get("categoria", "Sin datos"),
        "bt_min_k": resumen.get("bt_min_k"),
        "pico": resumen.get("pico"),
        "indicadores": {k: resumen.get(k) for k in
                        ("bt_min_k", "f215_max", "ot_max", "enfriamiento", "duracion_min")
                        if k in resumen},
        "serie": res.get("serie", []),
        "radio_km": radio_km,
    }


def evaluate_days(lat: float, lon: float, fechas: list[date],
                  radio_km: float = RADIO_KM_DEFECTO, progress=None) -> list[dict]:
    """Evalúa varias fechas candidatas. Cada una es una consulta a Earth Engine.

    Pensado para correr sólo sobre los días que el registro meteorológico ya
    marcó como sospechosos: barrer una campaña entera día por día sería caro y
    casi todo daría cero.
    """
    salida = []
    for i, f in enumerate(fechas):
        if progress:
            progress((i + 0.5) / max(1, len(fechas)), f"Analizando {f:%d/%m/%Y}…")
        try:
            salida.append(evaluate_event(lat, lon, f, radio_km))
        except HailEngineUnavailable:
            raise
        except Exception as exc:
            log.warning("Granizo %s: %s", f, exc)
            salida.append({"fecha": f, "score": None, "categoria": "Error", "error": str(exc)})
    return salida


def category_color(categoria: str) -> str:
    return {
        "Muy bajo": "#1baf7a", "Bajo": "#96bf4e", "Moderado": "#eda100",
        "Alto": "#eb6834", "Muy alto": "#d03b3b",
    }.get(categoria, "#898781")
