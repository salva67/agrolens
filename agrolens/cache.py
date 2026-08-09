"""Caché en disco.

Consultar Earth Engine o el archivo climático es caro; volver a abrir el
mismo lote no debería serlo. Este caché persiste entre corridas de la app,
a diferencia de `st.cache_data`, que muere con el proceso.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from .config import CACHE_DIR, SETTINGS

log = logging.getLogger(__name__)


def _default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return {k: v for k, v in vars(o).items() if not k.startswith("_")}
    return str(o)


def make_key(namespace: str, *args: Any, **kwargs: Any) -> str:
    raw = json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=_default).encode()
    return f"{namespace}-{hashlib.sha1(raw).hexdigest()[:24]}"


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.pkl"


def get(key: str, ttl_hours: float | None = None) -> Any | None:
    p = _path(key)
    if not p.exists():
        return None
    ttl = SETTINGS.cache_ttl_hours if ttl_hours is None else ttl_hours
    if ttl > 0 and (time.time() - p.stat().st_mtime) > ttl * 3600:
        return None
    try:
        with p.open("rb") as fh:
            return pickle.load(fh)
    except Exception as exc:  # caché corrupto: se descarta en silencio
        log.warning("Caché ilegible %s: %s", key, exc)
        p.unlink(missing_ok=True)
        return None


def put(key: str, value: Any) -> None:
    try:
        tmp = _path(key).with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(_path(key))
    except Exception as exc:  # pragma: no cover
        log.warning("No se pudo escribir el caché %s: %s", key, exc)


def disk_cache(namespace: str, ttl_hours: float | None = None) -> Callable:
    """Decorador de caché en disco.

    Pasar `refresh=True` a la función decorada fuerza el recálculo.
    """

    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, refresh: bool = False, **kwargs: Any) -> Any:
            key = make_key(f"{namespace}.{fn.__name__}", *args, **kwargs)
            if not refresh:
                hit = get(key, ttl_hours)
                if hit is not None:
                    return hit
            value = fn(*args, **kwargs)
            if value is not None:
                put(key, value)
            return value

        return wrapper

    return deco


def clear(namespace: str | None = None) -> int:
    """Borra el caché (todo o un namespace). Devuelve cuántos archivos eliminó.

    Las claves tienen la forma `namespace.funcion-hash`, así que el patrón no
    puede asumir que el guion viene justo después del namespace.
    """
    pattern = f"{namespace}*.pkl" if namespace else "*.pkl"
    n = 0
    for p in CACHE_DIR.glob(pattern):
        p.unlink(missing_ok=True)
        n += 1
    return n


def stats() -> dict[str, Any]:
    files = list(CACHE_DIR.glob("*.pkl"))
    size = sum(f.stat().st_size for f in files)
    return {
        "archivos": len(files),
        "tamaño_mb": round(size / 1e6, 2),
        "namespaces": sorted({f.name.rsplit("-", 1)[0] for f in files}),
    }
