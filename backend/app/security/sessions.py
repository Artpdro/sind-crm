import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import Connection, text

from app import timeutil
from app.config import get_settings
from app.db import get_db
from app.security.net import client_ip

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def cookie_name() -> str:
    # Prefixo __Host- exige Secure, Path=/ e sem Domain: impede injeção de cookie por subdomínio.
    return "__Host-session" if get_settings().cookie_secure else "session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class SessionUser:
    session_id: int
    csrf_token: str
    user_id: int
    username: str
    role: str
    must_change_password: bool


def create_session(db: Connection, request: Request, user_id: int, absolute_expiry_sql: str | None = None) -> tuple[str, str]:
    """Sempre gera token NOVO (evita session fixation). Retorna (token, csrf)."""
    s = get_settings()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(
        text(
            "INSERT INTO sessions (user_id, token_hash, csrf_token, ip, user_agent, created_at, last_seen_at, expires_at) "
            "VALUES (:u, :h, :c, :ip, :ua, :now, :now, :exp)"
        ),
        {
            "u": user_id, "h": _hash(token), "c": csrf, "ip": client_ip(request),
            "ua": (request.headers.get("user-agent") or "")[:300],
            "now": timeutil.now(), "exp": timeutil.now(hours=s.session_absolute_hours),
        },
    )
    return token, csrf


def set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        cookie_name(), token, httponly=True, secure=s.cookie_secure, samesite="lax", path="/",
        max_age=s.session_absolute_hours * 3600,
    )


def clear_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(cookie_name(), path="/", secure=s.cookie_secure, httponly=True, samesite="lax")


def resolve_session(request: Request, db: Connection) -> SessionUser | None:
    token = request.cookies.get(cookie_name())
    if not token or len(token) > 200:
        return None
    s = get_settings()
    row = db.execute(
        text(
            "SELECT s.id, s.csrf_token, s.last_seen_at, u.id AS uid, u.username, u.role, u.must_change_password "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = :h AND s.revoked_at IS NULL AND s.expires_at > :now "
            "AND s.last_seen_at > :idle AND u.is_active = 1"
        ),
        {"h": _hash(token), "now": timeutil.now(), "idle": timeutil.now(minutes=-s.session_idle_minutes)},
    ).mappings().first()
    if not row:
        return None
    db.execute(
        text("UPDATE sessions SET last_seen_at = :now WHERE id = :i AND last_seen_at < :cut"),
        {"i": row["id"], "now": timeutil.now(), "cut": timeutil.now(seconds=-60)},
    )
    db.commit()
    return SessionUser(row["id"], row["csrf_token"], row["uid"], row["username"], row["role"], bool(row["must_change_password"]))


def check_csrf(request: Request, user: SessionUser) -> None:
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent, user.csrf_token):
        raise HTTPException(status_code=403, detail="Token CSRF ausente ou inválido.")


def revoke_session(db: Connection, session_id: int) -> None:
    db.execute(text("UPDATE sessions SET revoked_at = :now WHERE id = :i AND revoked_at IS NULL"),
               {"i": session_id, "now": timeutil.now()})


def revoke_all_sessions(db: Connection, user_id: int, except_session_id: int | None = None) -> None:
    db.execute(
        text("UPDATE sessions SET revoked_at = :now WHERE user_id = :u AND revoked_at IS NULL AND id IS NOT :e"),
        {"u": user_id, "e": except_session_id, "now": timeutil.now()},
    )


def current_user_allow_pending(request: Request, db: Connection = Depends(get_db)) -> SessionUser:
    user = resolve_session(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    if request.method not in SAFE_METHODS:
        check_csrf(request, user)
    return user


def current_user(user: SessionUser = Depends(current_user_allow_pending)) -> SessionUser:
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Troca de senha obrigatória.")
    return user


def require_role(*roles: str):
    """Autorização SEMPRE no backend. Ex.: Depends(require_role('admin'))."""
    def dep(user: SessionUser = Depends(current_user)) -> SessionUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Sem permissão para esta operação.")
        return user
    return dep
