"""Configuración global de AgroLens.

Todo lo que sea "constante del sistema" vive acá: rutas, credenciales,
umbrales por defecto y la paleta de color. Ningún otro módulo debería
hardcodear un color, un umbral o una ruta.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "AgroLens"
APP_TAGLINE = "Monitoreo agrícola satelital y agroclimático"
APP_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    """Lee de variables de entorno y, si existe, de st.secrets."""
    val = os.getenv(name)
    if val:
        return val
    try:  # streamlit es opcional a nivel librería
        import streamlit as st

        if name in st.secrets:  # type: ignore[operator]
            return str(st.secrets[name])
    except Exception:
        pass
    return default


PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = PKG_DIR.parent

# Dónde viven lotes, caché e informes. Conviene sacarlo de cualquier carpeta
# sincronizada (OneDrive, Dropbox, Drive): SQLite usa bloqueos de archivo y el
# cliente de sincronización puede corromper la base mientras se escribe.
DATA_DIR = Path(_env("AGROLENS_DATA_DIR") or (ROOT_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "agrolens.sqlite"
ASSETS_DIR = PKG_DIR / "assets"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, EXPORT_DIR, ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()


def in_synced_folder() -> str | None:
    """Devuelve el servicio de sincronización si los datos están dentro de uno."""
    ruta = str(DATA_DIR).lower()
    for marca, nombre in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"),
                          ("google drive", "Google Drive"), ("\\gdrive", "Google Drive")):
        if marca in ruta:
            return nombre
    return None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    # Earth Engine
    # Sin valor por defecto a propósito: cada instalación define el suyo por
    # EE_PROJECT (entorno o secrets). Un id hardcodeado haría que otra persona
    # que clone el repo apunte en silencio a un proyecto que no es suyo.
    ee_project: str = _env("EE_PROJECT", "")
    ee_service_account_json: str = _env("EE_SERVICE_ACCOUNT_JSON", "")

    # Sentinel-2
    s2_collection: str = "COPERNICUS/S2_SR_HARMONIZED"
    s2_cloud_prob_collection: str = "COPERNICUS/S2_CLOUD_PROBABILITY"
    s2_scale_m: int = 10
    max_scene_cloud_pct: float = 60.0  # filtro grueso a nivel escena
    min_valid_fraction: float = 0.60  # fracción mínima de píxeles válidos en el lote
    cloud_prob_threshold: int = 40  # % s2cloudless
    cloud_buffer_m: int = 60  # dilatación de nubes y sombras

    # Clima
    weather_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    weather_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    climatology_start: str = "1991-01-01"
    climatology_end: str = "2020-12-31"
    forecast_days: int = 16

    # Analítica
    smoothing_window_days: int = 21
    smoothing_polyorder: int = 2
    default_soil_awc_mm: float = 150.0  # agua útil del perfil explorado
    n_zones_default: int = 3
    history_years: int = 6

    # Runtime
    cache_ttl_hours: int = 24
    request_timeout_s: int = 60
    max_download_px: int = 4_000_000
    demo_mode: bool = _env("AGROLENS_DEMO", "0") == "1"


SETTINGS = Settings()


# --------------------------------------------------------------------------
# Paleta (instancia de referencia validada — ver docs/dataviz)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    surface: str
    page: str
    text_primary: str
    text_secondary: str
    muted: str
    grid: str
    axis: str
    series: tuple[str, ...]
    good: str = "#0ca30c"
    warning: str = "#fab219"
    serious: str = "#ec835a"
    critical: str = "#d03b3b"

    def s(self, i: int) -> str:
        """Slot categórico i (0-based), en orden fijo, nunca cíclico más allá de 8."""
        return self.series[i % len(self.series)]


LIGHT = Palette(
    surface="#fcfcfb",
    page="#f9f9f7",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
)

DARK = Palette(
    surface="#1a1a19",
    page="#0d0d0d",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
)

# Rampa secuencial azul (magnitud continua) — 100 → 700
SEQ_BLUE = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
    "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
    "#184f95", "#104281", "#0d366b",
]

# Divergente azul ↔ rojo con gris neutro al medio (anomalías)
DIV_ANOM_LIGHT = ["#0d366b", "#2a78d6", "#9ec5f4", "#f0efec", "#f0a3a2", "#e34948", "#8f1d1c"]
DIV_ANOM_DARK = ["#0d366b", "#3987e5", "#9ec5f4", "#383835", "#f0a3a2", "#e66767", "#8f1d1c"]

# Rampa de vegetación para mapas (marrón → verde). Se usa SOLO en mapas
# raster, donde el significado es magnitud y no identidad de serie.
VEG_RAMP = [
    "#8c6b3f", "#b79a5e", "#d8cf8b", "#c3d16f", "#96bf4e",
    "#65a83a", "#3d8f2c", "#1f7522", "#0d5c1c", "#04400f",
]

WATER_RAMP = ["#7a4a12", "#b07d2c", "#d9c07a", "#e8e8e8", "#8ec6e0", "#3d8fc4", "#12508f"]


def palette(dark: bool = False) -> Palette:
    return DARK if dark else LIGHT


# --------------------------------------------------------------------------
# Basemaps
# --------------------------------------------------------------------------
BASEMAPS: dict[str, dict[str, str]] = {
    "Satélite (Esri)": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri, Maxar, Earthstar Geographics",
    },
    "Satélite + calles (Google)": {
        "url": "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        "attr": "Google",
    },
    "Mapa claro (Carto)": {
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "OpenStreetMap, CARTO",
    },
    "OpenStreetMap": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "OpenStreetMap contributors",
    },
}

DEFAULT_CENTER = (-36.75, -62.95)  # Salliqueló, Buenos Aires
DEFAULT_ZOOM = 12
