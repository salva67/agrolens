"""Modelos de dominio de AgroLens."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

Severity = str  # "info" | "good" | "warning" | "serious" | "critical"

SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2, "info": 3, "good": 4}
SEVERITY_LABEL = {
    "critical": "Crítico",
    "serious": "Importante",
    "warning": "Atención",
    "info": "Informativo",
    "good": "Favorable",
}
SEVERITY_ICON = {"critical": "🔴", "serious": "🟠", "warning": "🟡", "info": "🔵", "good": "🟢"}


def _iso(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


@dataclass
class Lote:
    """Un lote: geometría + metadatos agronómicos."""

    name: str
    geometry: dict  # GeoJSON geometry (EPSG:4326)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    farm: str = ""
    crop: str = "otro"
    variety: str = ""
    sowing_date: date | None = None
    harvest_date: date | None = None
    soil_texture: str = "Franco"
    soil_awc_mm: float = 150.0
    yield_target_tha: float = 0.0
    notes: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    # Dueño del lote (email de la cuenta que lo creó). Lo asigna la capa de
    # persistencia; no se edita desde el formulario.
    owner: str = ""
    # Nivel de acceso del usuario actual: "dueño" | "edicion" | "lectura".
    # Es un dato de la consulta, no del lote: no se guarda.
    access: str = "dueño"

    # Calculados
    area_ha: float = 0.0
    centroid: tuple[float, float] = (0.0, 0.0)  # (lat, lon)

    @property
    def geom_hash(self) -> str:
        raw = json.dumps(self.geometry, sort_keys=True).encode()
        return hashlib.sha1(raw).hexdigest()[:16]

    @property
    def display(self) -> str:
        return f"{self.name} · {self.farm}" if self.farm else self.name

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sowing_date"] = _iso(self.sowing_date)
        d["harvest_date"] = _iso(self.harvest_date)
        d["created_at"] = _iso(self.created_at)
        d["centroid"] = list(self.centroid)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Lote":
        d = dict(d)
        for k in ("sowing_date", "harvest_date"):
            if isinstance(d.get(k), str) and d[k]:
                d[k] = date.fromisoformat(d[k][:10])
            elif not d.get(k):
                d[k] = None
        if isinstance(d.get("created_at"), str):
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        if isinstance(d.get("centroid"), list):
            d["centroid"] = tuple(d["centroid"])
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Alert:
    """Hallazgo accionable emitido por el motor de reglas."""

    code: str
    severity: Severity
    title: str
    detail: str
    recommendation: str = ""
    value: float | None = None
    source: str = ""  # "satelital" | "clima" | "agronomía"

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 9)


@dataclass
class Phenology:
    """Métricas fenológicas derivadas de la curva del índice."""

    sos: date | None = None  # inicio de temporada
    pos: date | None = None  # pico
    eos: date | None = None  # fin de temporada
    peak_value: float | None = None
    integral: float | None = None  # área bajo la curva (índice·día)
    green_up_rate: float | None = None  # unidades de índice por día
    senescence_rate: float | None = None
    length_days: int | None = None


@dataclass
class ZoneStats:
    zone: int
    label: str
    area_ha: float
    pct: float
    mean: float
    std: float
    color: str


@dataclass
class AnalysisConfig:
    """Parámetros de una corrida de análisis."""

    start: date
    end: date
    index: str = "NDVI"
    cloud_pct: float = 60.0
    min_valid_fraction: float = 0.6
    n_zones: int = 3
    history_years: int = 6
    smoothing_days: int = 21

    @property
    def cache_key(self) -> str:
        raw = json.dumps({k: _iso(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


# El coeficiente de variación se dispara cuando la media del índice es baja
# (suelo desnudo, senescencia): un desvío de 0,1 sobre una media de 0,2 da un
# CV de 50 % sin que el lote sea heterogéneo. Por debajo de este piso la
# uniformidad no significa nada y se informa como "sin dato".
UNIFORMITY_MIN_MEAN = 0.25
UNIFORMITY_CV_SCALE = 0.45


def uniformity_score(mean: float, std: float) -> float:
    """0–100. Cuánto se parece el lote a sí mismo (100 = homogéneo)."""
    import math

    if not mean or mean < UNIFORMITY_MIN_MEAN:
        return math.nan
    cv = std / mean
    return float(max(0.0, min(100.0, 100.0 * (1.0 - cv / UNIFORMITY_CV_SCALE))))


@dataclass
class SceneInfo:
    """Una observación satelital válida sobre el lote."""

    date: date
    scene_id: str
    cloud_scene_pct: float
    valid_fraction: float
    mean: float
    median: float
    p10: float
    p90: float
    std: float
    min: float
    max: float

    @property
    def cv(self) -> float:
        return float(self.std / self.mean) if self.mean else 0.0

    @property
    def uniformity(self) -> float:
        return uniformity_score(self.mean, self.std)
