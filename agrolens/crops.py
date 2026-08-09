"""Parámetros agronómicos por cultivo.

Los valores son referencias para la región pampeana argentina (secano).
Se usan para grados-día, coeficiente de cultivo (Kc), balance hídrico,
etapas fenológicas y el modelo orientativo de rendimiento.

Fuentes de referencia: FAO-56 (Kc, profundidad radicular, coef. de respuesta
al agua Ky), INTA (ciclos y sumas térmicas de la región pampeana).
Cada valor es un promedio de grupo de madurez medio: se puede ajustar por
lote desde la UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stage:
    """Etapa fenológica definida como fracción de la suma térmica del ciclo."""

    name: str
    gdd_frac: float  # fracción acumulada de GDD a la que ARRANCA la etapa
    short: str


@dataclass(frozen=True)
class Crop:
    key: str
    label: str
    t_base: float  # temperatura base para GDD (°C)
    t_upper: float  # tope superior de acumulación (°C)
    gdd_cycle: float  # suma térmica total del ciclo (°C día)
    cycle_days: int  # duración típica del ciclo (días)
    kc_ini: float
    kc_mid: float
    kc_end: float
    root_depth_mm: int  # profundidad efectiva de raíces
    ky: float  # coef. de respuesta del rendimiento al déficit hídrico (FAO-56)
    ndvi_peak: float  # NDVI típico en plena cobertura
    yield_ref_tha: float  # rendimiento de referencia con iNDVI de referencia
    indvi_ref: float  # integral de NDVI (unidades NDVI·día) de un lote de referencia
    frost_critical_c: float  # temperatura mínima que produce daño
    heat_critical_c: float  # temperatura máxima que produce daño en período crítico
    critical_window: tuple[float, float]  # ventana crítica como fracción de GDD
    stages: tuple[Stage, ...] = field(default_factory=tuple)

    def stage_at(self, gdd_acc: float) -> Stage | None:
        """Etapa fenológica correspondiente a una suma térmica acumulada."""
        if not self.stages:
            return None
        frac = gdd_acc / self.gdd_cycle if self.gdd_cycle else 0.0
        current = self.stages[0]
        for st in self.stages:
            if frac >= st.gdd_frac:
                current = st
        return current

    def kc_at(self, frac: float) -> float:
        """Kc interpolado por fracción del ciclo (curva trapezoidal FAO-56)."""
        frac = max(0.0, min(1.0, frac))
        if frac < 0.15:
            return self.kc_ini
        if frac < 0.45:  # desarrollo: ini → mid
            t = (frac - 0.15) / 0.30
            return self.kc_ini + t * (self.kc_mid - self.kc_ini)
        if frac < 0.75:  # mediados de temporada
            return self.kc_mid
        t = (frac - 0.75) / 0.25  # maduración: mid → end
        return self.kc_mid + t * (self.kc_end - self.kc_mid)


def _stages(pairs: list[tuple[str, float, str]]) -> tuple[Stage, ...]:
    return tuple(Stage(name=n, gdd_frac=f, short=s) for n, f, s in pairs)


CROPS: dict[str, Crop] = {
    "soja": Crop(
        key="soja", label="Soja", t_base=10.0, t_upper=30.0, gdd_cycle=1450, cycle_days=145,
        kc_ini=0.40, kc_mid=1.15, kc_end=0.50, root_depth_mm=1000, ky=0.85,
        ndvi_peak=0.87, yield_ref_tha=3.6, indvi_ref=68.0,
        frost_critical_c=0.0, heat_critical_c=35.0, critical_window=(0.55, 0.80),
        stages=_stages([
            ("Emergencia", 0.00, "VE"), ("Vegetativo", 0.10, "V"),
            ("Floración", 0.42, "R1-R2"), ("Formación de vainas", 0.55, "R3-R4"),
            ("Llenado de granos", 0.66, "R5-R6"), ("Madurez", 0.88, "R7-R8"),
        ]),
    ),
    "maiz": Crop(
        key="maiz", label="Maíz", t_base=8.0, t_upper=32.0, gdd_cycle=1700, cycle_days=155,
        kc_ini=0.40, kc_mid=1.20, kc_end=0.55, root_depth_mm=1200, ky=1.25,
        ndvi_peak=0.90, yield_ref_tha=9.0, indvi_ref=78.0,
        frost_critical_c=0.5, heat_critical_c=35.0, critical_window=(0.45, 0.65),
        stages=_stages([
            ("Emergencia", 0.00, "VE"), ("Vegetativo", 0.08, "V6-V12"),
            ("Crítico (floración)", 0.45, "VT-R1"), ("Llenado", 0.60, "R2-R4"),
            ("Madurez fisiológica", 0.85, "R6"),
        ]),
    ),
    "trigo": Crop(
        key="trigo", label="Trigo", t_base=0.0, t_upper=26.0, gdd_cycle=2100, cycle_days=175,
        kc_ini=0.35, kc_mid=1.15, kc_end=0.35, root_depth_mm=1200, ky=1.05,
        ndvi_peak=0.85, yield_ref_tha=4.5, indvi_ref=72.0,
        frost_critical_c=-2.0, heat_critical_c=32.0, critical_window=(0.55, 0.78),
        stages=_stages([
            ("Emergencia", 0.00, "Z1"), ("Macollaje", 0.15, "Z2"),
            ("Encañazón", 0.40, "Z3"), ("Espigazón", 0.58, "Z5"),
            ("Llenado", 0.70, "Z7"), ("Madurez", 0.90, "Z9"),
        ]),
    ),
    "cebada": Crop(
        key="cebada", label="Cebada", t_base=0.0, t_upper=26.0, gdd_cycle=1900, cycle_days=160,
        kc_ini=0.35, kc_mid=1.15, kc_end=0.30, root_depth_mm=1100, ky=1.00,
        ndvi_peak=0.84, yield_ref_tha=4.8, indvi_ref=68.0,
        frost_critical_c=-2.0, heat_critical_c=32.0, critical_window=(0.55, 0.78),
        stages=_stages([
            ("Emergencia", 0.00, "Z1"), ("Macollaje", 0.15, "Z2"),
            ("Encañazón", 0.40, "Z3"), ("Espigazón", 0.58, "Z5"),
            ("Llenado", 0.70, "Z7"), ("Madurez", 0.90, "Z9"),
        ]),
    ),
    "girasol": Crop(
        key="girasol", label="Girasol", t_base=6.0, t_upper=32.0, gdd_cycle=1550, cycle_days=135,
        kc_ini=0.35, kc_mid=1.05, kc_end=0.35, root_depth_mm=1300, ky=0.95,
        ndvi_peak=0.82, yield_ref_tha=2.6, indvi_ref=58.0,
        frost_critical_c=0.0, heat_critical_c=36.0, critical_window=(0.50, 0.72),
        stages=_stages([
            ("Emergencia", 0.00, "VE"), ("Vegetativo", 0.10, "V"),
            ("Botón floral", 0.42, "R1-R4"), ("Floración", 0.52, "R5"),
            ("Llenado", 0.68, "R6-R8"), ("Madurez", 0.88, "R9"),
        ]),
    ),
    "sorgo": Crop(
        key="sorgo", label="Sorgo", t_base=10.0, t_upper=34.0, gdd_cycle=1600, cycle_days=140,
        kc_ini=0.35, kc_mid=1.05, kc_end=0.55, root_depth_mm=1500, ky=0.90,
        ndvi_peak=0.86, yield_ref_tha=6.0, indvi_ref=66.0,
        frost_critical_c=0.5, heat_critical_c=38.0, critical_window=(0.50, 0.72),
        stages=_stages([
            ("Emergencia", 0.00, "VE"), ("Vegetativo", 0.10, "V"),
            ("Panojamiento", 0.48, "PA"), ("Llenado", 0.65, "GL"),
            ("Madurez", 0.88, "MF"),
        ]),
    ),
    "alfalfa": Crop(
        key="alfalfa", label="Alfalfa / pastura", t_base=5.0, t_upper=30.0, gdd_cycle=900,
        cycle_days=365, kc_ini=0.40, kc_mid=1.15, kc_end=1.10, root_depth_mm=1500, ky=1.10,
        ndvi_peak=0.85, yield_ref_tha=12.0, indvi_ref=180.0,
        frost_critical_c=-5.0, heat_critical_c=38.0, critical_window=(0.30, 0.90),
        stages=_stages([("Rebrote", 0.00, "RB"), ("Crecimiento", 0.20, "CR"), ("Corte", 0.80, "CO")]),
    ),
    "otro": Crop(
        key="otro", label="Otro / sin definir", t_base=8.0, t_upper=32.0, gdd_cycle=1600,
        cycle_days=150, kc_ini=0.40, kc_mid=1.10, kc_end=0.50, root_depth_mm=1000, ky=1.00,
        ndvi_peak=0.85, yield_ref_tha=0.0, indvi_ref=70.0,
        frost_critical_c=0.0, heat_critical_c=35.0, critical_window=(0.45, 0.75),
        stages=_stages([("Inicio", 0.00, "I"), ("Desarrollo", 0.15, "D"),
                        ("Plena cobertura", 0.45, "PC"), ("Senescencia", 0.80, "S")]),
    ),
}

CROP_LABELS = {c.key: c.label for c in CROPS.values()}


def get_crop(key: str | None) -> Crop:
    if not key:
        return CROPS["otro"]
    k = key.strip().lower()
    if k in CROPS:
        return CROPS[k]
    for crop in CROPS.values():  # tolerante a etiquetas ("Maíz", "MAIZ")
        if crop.label.lower().startswith(k[:4]):
            return crop
    return CROPS["otro"]


# Textura de suelo → agua útil (mm por metro de perfil). Referencia rápida
# para el balance hídrico cuando el usuario no conoce el dato de análisis.
SOIL_AWC_MM_PER_M: dict[str, float] = {
    "Arenoso": 70.0,
    "Franco arenoso": 110.0,
    "Franco": 150.0,
    "Franco limoso": 170.0,
    "Franco arcilloso": 160.0,
    "Arcilloso": 140.0,
}
