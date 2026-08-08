"""Identidad y control de acceso.

Tres modos, elegidos automáticamente según lo que esté configurado:

* **OIDC** (recomendado para publicar): si `secrets.toml` tiene una sección
  `[auth]`, se usa el login nativo de Streamlit contra Google u otro proveedor.
  Las contraseñas nunca pasan por acá.
* **Clave compartida**: si sólo hay `AGROLENS_PASSWORD`, una única clave para
  todos. Los lotes quedan bajo una identidad común.
* **Local**: sin nada configurado, la app corre con la identidad `local` y sin
  pedir nada. Es el modo de trabajo en tu propia máquina.

La lista blanca (`AGROLENS_ALLOWED_EMAILS` / `AGROLENS_ALLOWED_DOMAIN`) es
importante: sin ella, cualquiera con una cuenta de Google puede entrar a una
app publicada.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import streamlit as st

from ..config import APP_NAME, APP_TAGLINE, _env

LOCAL_USER = "local"


@dataclass(frozen=True)
class User:
    email: str
    name: str = ""
    picture: str = ""
    modo: str = "local"  # "oidc" | "clave" | "local"

    @property
    def display(self) -> str:
        return self.name or self.email

    @property
    def initials(self) -> str:
        base = (self.name or self.email).strip()
        partes = [p for p in base.replace(".", " ").split() if p]
        return "".join(p[0] for p in partes[:2]).upper() or "?"


# --------------------------------------------------------------------------
# Modo de autenticación
# --------------------------------------------------------------------------
def oidc_configured() -> bool:
    try:
        return "auth" in st.secrets and bool(st.secrets["auth"].get("client_id"))
    except Exception:
        return False


def password_configured() -> bool:
    return bool(_env("AGROLENS_PASSWORD", ""))


def mode() -> str:
    if oidc_configured():
        return "oidc"
    if password_configured():
        return "clave"
    return "local"


# --------------------------------------------------------------------------
# Lista blanca
# --------------------------------------------------------------------------
def _allowlist() -> tuple[set[str], set[str]]:
    emails = {e.strip().lower() for e in _env("AGROLENS_ALLOWED_EMAILS", "").split(",") if e.strip()}
    dominios = {d.strip().lower().lstrip("@")
                for d in _env("AGROLENS_ALLOWED_DOMAIN", "").split(",") if d.strip()}
    return emails, dominios


def is_allowed(email: str) -> bool:
    """Sin lista blanca configurada, entra cualquiera que se autentique."""
    emails, dominios = _allowlist()
    if not emails and not dominios:
        return True
    e = (email or "").strip().lower()
    return e in emails or any(e.endswith("@" + d) for d in dominios)


# --------------------------------------------------------------------------
# Sesión
# --------------------------------------------------------------------------
def current_user() -> User | None:
    """Usuario de la sesión actual, o None si todavía no se autenticó."""
    m = mode()
    if m == "local":
        return User(email=LOCAL_USER, name="Sesión local", modo="local")
    if m == "clave":
        if st.session_state.get("_auth_ok"):
            return User(email=_env("AGROLENS_SHARED_ACCOUNT", "equipo"),
                        name="Cuenta compartida", modo="clave")
        return None
    try:
        if not st.user.is_logged_in:
            return None
        email = (getattr(st.user, "email", "") or "").strip().lower()
        if not email:
            return None
        return User(email=email, name=getattr(st.user, "name", "") or "",
                    picture=getattr(st.user, "picture", "") or "", modo="oidc")
    except Exception:
        return None


def gate() -> User:
    """Corta la ejecución hasta tener una sesión válida. Devuelve el usuario."""
    m = mode()
    user = current_user()

    if user is not None and (m != "oidc" or is_allowed(user.email)):
        _claim_once(user)
        return user

    if user is not None and m == "oidc":  # autenticado pero fuera de la lista
        _pantalla(
            f"La cuenta **{user.email}** no está habilitada para esta aplicación.",
            boton=("Salir", st.logout),
        )
        st.stop()

    if m == "oidc":
        _pantalla("Entrá con tu cuenta de Google para ver tus lotes.",
                  boton=("Iniciar sesión con Google", st.login))
        st.stop()

    _pantalla_clave()
    st.stop()


def _pantalla(mensaje: str, boton: tuple[str, object] | None = None) -> None:
    st.markdown(f"### {APP_NAME}")
    st.caption(APP_TAGLINE)
    st.write(mensaje)
    if boton:
        etiqueta, accion = boton
        st.button(etiqueta, type="primary", on_click=accion)  # type: ignore[arg-type]


def _pantalla_clave() -> None:
    st.markdown(f"### {APP_NAME}")
    st.caption(APP_TAGLINE)
    with st.form("acceso"):
        clave = st.text_input("Clave de acceso", type="password")
        enviado = st.form_submit_button("Entrar", type="primary")
    if enviado:
        # compare_digest evita filtrar la clave por el tiempo de comparación
        if hmac.compare_digest(clave, _env("AGROLENS_PASSWORD", "")):
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("Clave incorrecta.")


def _claim_once(user: User) -> None:
    """Adopta los lotes anteriores al modelo multiusuario, una sola vez por sesión."""
    if st.session_state.get("_claimed"):
        return
    st.session_state["_claimed"] = True
    try:
        from .. import storage

        n = storage.claim_orphans(user.email)
        if n:
            st.toast(f"Se asignaron {n} lote(s) previos a tu cuenta.", icon="📥")
    except Exception:
        pass


def sidebar_account(user: User) -> None:
    """Tarjeta de cuenta al pie de la barra lateral."""
    with st.sidebar:
        st.divider()
        col_a, col_b = st.columns([1, 3])
        with col_a:
            if user.picture:
                st.image(user.picture, width=38)
            else:
                st.markdown(
                    f'<div style="width:38px;height:38px;border-radius:50%;'
                    f'background:rgba(128,128,128,.2);display:flex;align-items:center;'
                    f'justify-content:center;font-weight:600">{user.initials}</div>',
                    unsafe_allow_html=True,
                )
        with col_b:
            st.markdown(f"**{user.display}**")
            if user.modo == "oidc":
                st.caption(user.email)
            elif user.modo == "local":
                st.caption("modo local · sin autenticación")
            else:
                st.caption("cuenta compartida")

        if user.modo == "oidc":
            st.button("Cerrar sesión", on_click=st.logout, use_container_width=True)
        elif user.modo == "clave":
            if st.button("Salir", use_container_width=True):
                st.session_state.pop("_auth_ok", None)
                st.rerun()
