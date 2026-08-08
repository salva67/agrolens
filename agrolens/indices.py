"""Catálogo de índices espectrales.

Cada índice se define UNA sola vez con una fórmula agnóstica del motor: la
misma función se evalúa con imágenes de Earth Engine o con arrays de numpy,
inyectando el objeto `ops` correspondiente. Así no hay riesgo de que el mapa
y la serie temporal usen fórmulas distintas.

Todos los índices están disponibles siempre. No hay índices "premium".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .config import SEQ_BLUE, VEG_RAMP, WATER_RAMP

# Roles de banda → banda Sentinel-2 (colección SR armonizada)
BAND_MAP: dict[str, str] = {
    "blue": "B2",
    "green": "B3",
    "red": "B4",
    "re1": "B5",
    "re2": "B6",
    "re3": "B7",
    "nir": "B8",
    "nir8a": "B8A",
    "swir1": "B11",
    "swir2": "B12",
}


class Ops(Protocol):
    """Operaciones que no son operadores de Python y difieren entre motores."""

    def sqrt(self, x: Any) -> Any: ...
    def pow(self, x: Any, n: float) -> Any: ...


class NumpyOps:
    def sqrt(self, x):  # noqa: D102
        import numpy as np

        return np.sqrt(np.clip(x, 0, None))

    def pow(self, x, n):  # noqa: D102
        return x**n


class EEImage:
    """Adaptador aritmético para `ee.Image`.

    La API de Earth Engine en Python no sobrecarga `+ - * /`: hay que llamar a
    `.add()`, `.subtract()`, etc. Este envoltorio traduce los operadores, para
    que las fórmulas de arriba se escriban una sola vez y sirvan igual con
    numpy que con Earth Engine.
    """

    __slots__ = ("img",)

    def __init__(self, img: Any) -> None:
        self.img = img

    @staticmethod
    def _raw(other: Any) -> Any:
        return other.img if isinstance(other, EEImage) else other

    def _const(self, other: Any) -> Any:
        import ee

        return other.img if isinstance(other, EEImage) else ee.Image.constant(other)

    def __add__(self, o): return EEImage(self.img.add(self._raw(o)))
    def __radd__(self, o): return EEImage(self.img.add(self._raw(o)))
    def __sub__(self, o): return EEImage(self.img.subtract(self._raw(o)))
    def __rsub__(self, o): return EEImage(self._const(o).subtract(self.img))
    def __mul__(self, o): return EEImage(self.img.multiply(self._raw(o)))
    def __rmul__(self, o): return EEImage(self.img.multiply(self._raw(o)))
    def __truediv__(self, o): return EEImage(self.img.divide(self._raw(o)))
    def __rtruediv__(self, o): return EEImage(self._const(o).divide(self.img))
    def __pow__(self, n): return EEImage(self.img.pow(n))
    def __neg__(self): return EEImage(self.img.multiply(-1))


class EEOps:
    def sqrt(self, x):  # noqa: D102
        img = x.img if isinstance(x, EEImage) else x
        return EEImage(img.max(0).sqrt())

    def pow(self, x, n):  # noqa: D102
        img = x.img if isinstance(x, EEImage) else x
        return EEImage(img.pow(n))


@dataclass(frozen=True)
class Index:
    key: str
    label: str
    family: str  # "vigor" | "agua" | "suelo" | "clorofila"
    bands: tuple[str, ...]
    formula: Callable[[dict, Ops], Any]
    vmin: float
    vmax: float
    ramp: tuple[str, ...]
    unit: str
    summary: str  # qué mide, en una línea
    reading: str  # cómo se lee a campo
    higher_is_better: bool = True
    decimals: int = 3

    def compute(self, bands: dict, ops: Ops) -> Any:
        return self.formula(bands, ops)


def _ndvi(b, o):
    return (b["nir"] - b["red"]) / (b["nir"] + b["red"])


def _gndvi(b, o):
    return (b["nir"] - b["green"]) / (b["nir"] + b["green"])


def _ndre(b, o):
    return (b["nir8a"] - b["re1"]) / (b["nir8a"] + b["re1"])


def _evi2(b, o):
    return (b["nir"] - b["red"]) * 2.5 / (b["nir"] + b["red"] * 2.4 + 1.0)


def _savi(b, o):
    return (b["nir"] - b["red"]) * 1.5 / (b["nir"] + b["red"] + 0.5)


def _msavi2(b, o):
    t = b["nir"] * 2.0 + 1.0
    return (t - o.sqrt(o.pow(t, 2) - (b["nir"] - b["red"]) * 8.0)) / 2.0


def _ndmi(b, o):
    return (b["nir"] - b["swir1"]) / (b["nir"] + b["swir1"])


def _ndwi(b, o):
    return (b["green"] - b["nir"]) / (b["green"] + b["nir"])


def _nbr(b, o):
    return (b["nir"] - b["swir2"]) / (b["nir"] + b["swir2"])


def _bsi(b, o):
    num = (b["swir1"] + b["red"]) - (b["nir"] + b["blue"])
    den = (b["swir1"] + b["red"]) + (b["nir"] + b["blue"])
    return num / den


def _cire(b, o):
    return b["nir8a"] / b["re1"] - 1.0


def _lai(b, o):
    """LAI aproximado a partir de EVI2 (relación empírica de Boegh et al.)."""
    return _evi2(b, o) * 3.618 - 0.118


INDICES: dict[str, Index] = {
    "NDVI": Index(
        key="NDVI", label="NDVI", family="vigor", bands=("nir", "red"), formula=_ndvi,
        vmin=0.0, vmax=0.95, ramp=tuple(VEG_RAMP), unit="",
        summary="Vigor y biomasa verde. El índice de referencia del monitoreo.",
        reading="Menos de 0,3 suelo o cultivo muy incipiente; 0,3–0,6 desarrollo; más de 0,7 plena cobertura. "
                "Se satura cuando el canopeo cierra.",
    ),
    "EVI2": Index(
        key="EVI2", label="EVI2", family="vigor", bands=("nir", "red"), formula=_evi2,
        vmin=0.0, vmax=1.0, ramp=tuple(VEG_RAMP), unit="",
        summary="Vigor con menos saturación que NDVI en canopeos densos.",
        reading="Útil en plena cobertura, cuando el NDVI ya no discrimina. Compara mejor lotes de alto rendimiento.",
    ),
    "NDRE": Index(
        key="NDRE", label="NDRE", family="clorofila", bands=("nir8a", "re1"), formula=_ndre,
        vmin=0.0, vmax=0.6, ramp=tuple(VEG_RAMP), unit="",
        summary="Clorofila y estado nitrogenado usando el borde rojo.",
        reading="Cae antes que el NDVI ante deficiencia de nitrógeno o estrés. Clave en maíz y trigo en macollaje.",
    ),
    "GNDVI": Index(
        key="GNDVI", label="GNDVI", family="clorofila", bands=("nir", "green"), formula=_gndvi,
        vmin=0.0, vmax=0.85, ramp=tuple(VEG_RAMP), unit="",
        summary="Sensible a clorofila; complementa al NDVI en canopeo cerrado.",
        reading="Buen detector de cambios finos de color. Diferencias mayores a 0,05 entre zonas suelen ser reales.",
    ),
    "CIre": Index(
        key="CIre", label="Índice de clorofila (borde rojo)", family="clorofila",
        bands=("nir8a", "re1"), formula=_cire, vmin=0.0, vmax=6.0, ramp=tuple(VEG_RAMP), unit="",
        summary="Proxy lineal del contenido de clorofila por unidad de área.",
        reading="No se satura. Es el mejor de la familia para dosis variable de nitrógeno.", decimals=2,
    ),
    "MSAVI2": Index(
        key="MSAVI2", label="MSAVI2", family="vigor", bands=("nir", "red"), formula=_msavi2,
        vmin=0.0, vmax=0.9, ramp=tuple(VEG_RAMP), unit="",
        summary="Vigor corregido por el brillo del suelo.",
        reading="El indicado en implantación y baja cobertura, donde el NDVI se confunde con el rastrojo.",
    ),
    "SAVI": Index(
        key="SAVI", label="SAVI", family="vigor", bands=("nir", "red"), formula=_savi,
        vmin=0.0, vmax=0.9, ramp=tuple(VEG_RAMP), unit="",
        summary="Vigor con ajuste fijo por suelo (L = 0,5).",
        reading="Alternativa clásica al MSAVI2 en etapas tempranas.",
    ),
    "NDMI": Index(
        key="NDMI", label="NDMI (humedad del canopeo)", family="agua",
        bands=("nir", "swir1"), formula=_ndmi, vmin=-0.3, vmax=0.6, ramp=tuple(WATER_RAMP), unit="",
        summary="Contenido de agua en el canopeo.",
        reading="La caída sostenida antecede al estrés hídrico visible. Bajo 0,1 en plena cobertura: alerta.",
    ),
    "NDWI": Index(
        key="NDWI", label="NDWI (agua libre)", family="agua", bands=("green", "nir"),
        formula=_ndwi, vmin=-0.6, vmax=0.6, ramp=tuple(WATER_RAMP), unit="",
        summary="Detecta agua libre en superficie.",
        reading="Valores positivos indican anegamiento. Sirve para descontar bajos de la superficie útil.",
    ),
    "BSI": Index(
        key="BSI", label="BSI (suelo desnudo)", family="suelo",
        bands=("swir1", "red", "nir", "blue"), formula=_bsi, vmin=-0.5, vmax=0.5,
        ramp=tuple(reversed(VEG_RAMP)), unit="", higher_is_better=False,
        summary="Proporción de suelo expuesto.",
        reading="Alto tras la cosecha o en fallas de siembra. Sirve para auditar la calidad de implantación.",
    ),
    "NBR": Index(
        key="NBR", label="NBR (quemado / residuo)", family="suelo", bands=("nir", "swir2"),
        formula=_nbr, vmin=-0.5, vmax=0.8, ramp=tuple(SEQ_BLUE), unit="",
        summary="Detecta quema de rastrojo y cambios bruscos de cobertura.",
        reading="Caídas fuertes entre fechas consecutivas indican quema, cosecha o laboreo.",
    ),
    "LAI": Index(
        key="LAI", label="LAI estimado", family="vigor", bands=("nir", "red"), formula=_lai,
        vmin=0.0, vmax=6.0, ramp=tuple(VEG_RAMP), unit="m²/m²", decimals=2,
        summary="Índice de área foliar estimado desde EVI2.",
        reading="Valor orientativo. Sirve para seguir la evolución del canopeo, no como medida absoluta.",
    ),
}

DEFAULT_INDEX = "NDVI"
MAP_INDEX_ORDER = ["NDVI", "EVI2", "NDRE", "GNDVI", "CIre", "MSAVI2", "SAVI", "NDMI", "NDWI", "BSI", "NBR", "LAI"]


def get_index(key: str) -> Index:
    return INDICES.get(key, INDICES[DEFAULT_INDEX])


def indices_by_family() -> dict[str, list[Index]]:
    out: dict[str, list[Index]] = {}
    for key in MAP_INDEX_ORDER:
        idx = INDICES[key]
        out.setdefault(idx.family, []).append(idx)
    return out


FAMILY_LABELS = {
    "vigor": "Vigor y biomasa",
    "clorofila": "Clorofila y nitrógeno",
    "agua": "Agua y estrés hídrico",
    "suelo": "Suelo y residuo",
}
