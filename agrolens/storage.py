"""Persistencia local de lotes (SQLite), con dueño y lotes compartidos.

Cada lote pertenece a la cuenta que lo creó. Un dueño puede compartir un lote
puntual con otra persona, en lectura o en edición. Todas las funciones piden
explícitamente quién está consultando: no hay forma de leer o escribir un lote
sin decir con qué identidad se hace.

La comprobación de permisos vive acá y no en la interfaz, para que no dependa
de que una página se acuerde de chequear.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .config import DB_PATH
from .geo import area_ha, centroid_latlon
from .models import Lote

DUEÑO, EDICION, LECTURA = "dueño", "edicion", "lectura"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lotes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    farm        TEXT,
    crop        TEXT,
    owner       TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lotes_owner ON lotes(owner);

CREATE TABLE IF NOT EXISTS lote_shares (
    lote_id     TEXT NOT NULL,
    email       TEXT NOT NULL,
    permiso     TEXT NOT NULL DEFAULT 'lectura',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (lote_id, email)
);
CREATE INDEX IF NOT EXISTS idx_shares_email ON lote_shares(email);

CREATE TABLE IF NOT EXISTS reportes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    meta        TEXT
);
CREATE INDEX IF NOT EXISTS idx_reportes_lote ON reportes(lote_id);
"""


class AccessDenied(PermissionError):
    """El usuario no tiene permiso sobre ese lote."""


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # La migración va ANTES del esquema: el esquema crea un índice sobre
        # `owner`, y sobre una base vieja esa columna todavía no existe.
        _migrate(con)
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _migrate(con: sqlite3.Connection) -> None:
    """Agrega la columna `owner` a bases creadas antes del modelo multiusuario.

    Los lotes que ya existían quedan con dueño vacío; `claim_orphans` los
    adopta cuando alguien inicia sesión, para que nadie pierda lo que cargó.
    """
    tablas = {r["name"] for r in
              con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "lotes" not in tablas:
        return  # base nueva: la crea el esquema, ya con la columna
    cols = {r["name"] for r in con.execute("PRAGMA table_info(lotes)")}
    if "owner" not in cols:
        con.execute("ALTER TABLE lotes ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        con.commit()


# --------------------------------------------------------------------------
# Permisos
# --------------------------------------------------------------------------
def access_level(lote_id: str, user: str) -> str | None:
    """Nivel de acceso del usuario sobre el lote, o None si no tiene ninguno."""
    user = _norm(user)
    with _conn() as con:
        row = con.execute("SELECT owner FROM lotes WHERE id = ?", (lote_id,)).fetchone()
        if row is None:
            return None
        if _norm(row["owner"]) == user:
            return DUEÑO
        share = con.execute(
            "SELECT permiso FROM lote_shares WHERE lote_id = ? AND email = ?", (lote_id, user)
        ).fetchone()
        return share["permiso"] if share else None


def _require(lote_id: str, user: str, niveles: tuple[str, ...]) -> str:
    nivel = access_level(lote_id, user)
    if nivel not in niveles:
        raise AccessDenied(
            "Ese lote no es tuyo o no tenés permiso suficiente sobre él."
            if nivel else "Ese lote no existe o no tenés acceso."
        )
    return nivel


# --------------------------------------------------------------------------
# Lotes
# --------------------------------------------------------------------------
def save_lote(lote: Lote, user: str) -> Lote:
    """Crea o actualiza un lote. Sólo el dueño o alguien con edición puede grabar."""
    user = _norm(user)
    if not user:
        raise AccessDenied("Hace falta una sesión iniciada para guardar lotes.")

    nivel = access_level(lote.id, user)
    if nivel is None:
        with _conn() as con:
            existe = con.execute("SELECT 1 FROM lotes WHERE id = ?", (lote.id,)).fetchone()
        if existe:
            raise AccessDenied("Ese lote pertenece a otra cuenta.")
        lote.owner = user  # alta: el dueño es quien lo crea
    elif nivel == LECTURA:
        raise AccessDenied("Tenés el lote compartido sólo en lectura.")

    lote.area_ha = area_ha(lote.geometry)
    lote.centroid = centroid_latlon(lote.geometry)
    owner = _norm(lote.owner) or user

    with _conn() as con:
        con.execute(
            "INSERT INTO lotes (id, name, farm, crop, owner, payload, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, farm=excluded.farm, "
            "crop=excluded.crop, payload=excluded.payload, updated_at=excluded.updated_at",
            (lote.id, lote.name, lote.farm, lote.crop, owner,
             json.dumps(lote.to_dict(), ensure_ascii=False), datetime.now().isoformat()),
        )
    lote.owner = owner
    lote.access = nivel or DUEÑO
    return lote


def _hydrate(payload: str, owner: str, access: str) -> Lote | None:
    try:
        lote = Lote.from_dict(json.loads(payload))
    except Exception:  # un registro roto no debe tumbar el listado
        return None
    lote.owner = owner
    lote.access = access
    return lote


def list_lotes(user: str, include_shared: bool = True) -> list[Lote]:
    """Lotes propios y, opcionalmente, los que otros compartieron con el usuario."""
    user = _norm(user)
    if not user:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT payload, owner, ? AS access FROM lotes WHERE owner = ? ORDER BY farm, name",
            (DUEÑO, user),
        ).fetchall()
        if include_shared:
            rows += con.execute(
                "SELECT l.payload, l.owner, s.permiso AS access FROM lotes l "
                "JOIN lote_shares s ON s.lote_id = l.id WHERE s.email = ? ORDER BY l.farm, l.name",
                (user,),
            ).fetchall()
    out = [_hydrate(r["payload"], r["owner"], r["access"]) for r in rows]
    return [l for l in out if l is not None]


def get_lote(lote_id: str, user: str) -> Lote | None:
    """Un lote, si el usuario tiene algún nivel de acceso sobre él."""
    user = _norm(user)
    nivel = access_level(lote_id, user)
    if nivel is None:
        return None
    with _conn() as con:
        row = con.execute("SELECT payload, owner FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    return _hydrate(row["payload"], row["owner"], nivel) if row else None


def delete_lote(lote_id: str, user: str) -> None:
    """Borra un lote. Sólo el dueño puede."""
    _require(lote_id, _norm(user), (DUEÑO,))
    with _conn() as con:
        con.execute("DELETE FROM lotes WHERE id = ?", (lote_id,))
        con.execute("DELETE FROM lote_shares WHERE lote_id = ?", (lote_id,))
        con.execute("DELETE FROM reportes WHERE lote_id = ?", (lote_id,))


def claim_orphans(user: str) -> int:
    """Adopta los lotes sin dueño que quedaron de antes del modelo multiusuario.

    Se ejecuta al iniciar sesión. Si la base ya nació multiusuario no hace nada.
    """
    user = _norm(user)
    if not user:
        return 0
    with _conn() as con:
        cur = con.execute("UPDATE lotes SET owner = ? WHERE owner = '' OR owner IS NULL", (user,))
        return cur.rowcount or 0


def count_local_lotes() -> int:
    """Cuántos lotes quedaron bajo la identidad `local` (uso sin autenticación)."""
    with _conn() as con:
        return con.execute("SELECT COUNT(*) AS n FROM lotes WHERE owner = 'local'").fetchone()["n"]


def claim_local(user: str) -> int:
    """Pasa a tu cuenta los lotes creados antes de configurar el login.

    A diferencia de `claim_orphans`, esto NO es automático: al publicar la app,
    el primer usuario que entrara se quedaría con los lotes de la máquina de
    desarrollo. Se dispara desde un botón, con el número a la vista.
    """
    user = _norm(user)
    if not user or user == "local":
        return 0
    with _conn() as con:
        cur = con.execute("UPDATE lotes SET owner = ? WHERE owner = 'local'", (user,))
        return cur.rowcount or 0


# --------------------------------------------------------------------------
# Compartir
# --------------------------------------------------------------------------
def share_lote(lote_id: str, user: str, email: str, permiso: str = LECTURA) -> None:
    """Comparte un lote con otra cuenta. Sólo el dueño puede compartir."""
    user, email = _norm(user), _norm(email)
    _require(lote_id, user, (DUEÑO,))
    if not email or "@" not in email:
        raise ValueError("Escribí un email válido.")
    if email == user:
        raise ValueError("Ese lote ya es tuyo.")
    if permiso not in (LECTURA, EDICION):
        raise ValueError(f"Permiso inválido: {permiso}")
    with _conn() as con:
        con.execute(
            "INSERT INTO lote_shares (lote_id, email, permiso, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(lote_id, email) DO UPDATE SET permiso = excluded.permiso",
            (lote_id, email, permiso, datetime.now().isoformat()),
        )


def unshare_lote(lote_id: str, user: str, email: str) -> None:
    _require(lote_id, _norm(user), (DUEÑO,))
    with _conn() as con:
        con.execute("DELETE FROM lote_shares WHERE lote_id = ? AND email = ?",
                    (lote_id, _norm(email)))


def list_shares(lote_id: str, user: str) -> list[dict]:
    """Con quién está compartido un lote. Sólo lo ve el dueño."""
    _require(lote_id, _norm(user), (DUEÑO,))
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT email, permiso, created_at FROM lote_shares WHERE lote_id = ? ORDER BY email",
            (lote_id,),
        )]


# --------------------------------------------------------------------------
# Informes
# --------------------------------------------------------------------------
def register_report(lote_id: str, kind: str, path: str, meta: dict | None = None) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO reportes (lote_id, created_at, kind, path, meta) VALUES (?,?,?,?,?)",
            (lote_id, datetime.now().isoformat(), kind, str(path),
             json.dumps(meta or {}, ensure_ascii=False)),
        )


def list_reports(lote_id: str | None = None, limit: int = 50) -> list[dict]:
    """Informes de un lote. El control de acceso lo hace quien llama, al abrir el lote."""
    q = "SELECT * FROM reportes"
    params: tuple = ()
    if lote_id:
        q += " WHERE lote_id = ?"
        params = (lote_id,)
    q += " ORDER BY created_at DESC LIMIT ?"
    params = params + (limit,)
    with _conn() as con:
        return [dict(r) for r in con.execute(q, params).fetchall()]


def stats(user: str) -> dict:
    """Resumen para la página de ajustes."""
    user = _norm(user)
    propios = list_lotes(user, include_shared=False)
    compartidos = [l for l in list_lotes(user) if l.access != DUEÑO]
    with _conn() as con:
        cedidos = con.execute(
            "SELECT COUNT(*) AS n FROM lote_shares s JOIN lotes l ON l.id = s.lote_id "
            "WHERE l.owner = ?", (user,)
        ).fetchone()["n"]
    return {
        "propios": len(propios),
        "compartidos_conmigo": len(compartidos),
        "compartidos_por_mi": int(cedidos),
        "hectareas": sum(l.area_ha for l in propios),
    }
