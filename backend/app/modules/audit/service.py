import json

from fastapi import Request
from sqlalchemy import Connection, text

from app import timeutil
from app.security.net import client_ip

_SENSITIVE = ("password", "senha", "token", "secret", "csrf", "hash", "cookie", "authorization")


def _scrub(value):
    """Remove qualquer chave sensível dos detalhes antes de gravar (nunca logar segredos)."""
    if isinstance(value, dict):
        return {k: ("[removido]" if any(s in k.lower() for s in _SENSITIVE) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def record(
    db: Connection,
    request: Request | None,
    action: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
) -> None:
    db.execute(
        text(
            "INSERT INTO audit_log (occurred_at, user_id, username, action, entity_type, entity_id, ip, details) "
            "VALUES (:now, :uid, :un, :a, :et, :eid, :ip, :d)"
        ),
        {
            "now": timeutil.now(),
            "uid": user_id,
            "un": (username or "")[:150] or None,
            "a": action,
            "et": entity_type,
            "eid": entity_id,
            "ip": client_ip(request) if request else None,
            "d": json.dumps(_scrub(details or {}), default=str),
        },
    )
