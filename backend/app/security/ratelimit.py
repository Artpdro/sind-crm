"""Rate limit guardado no banco. As transações são serializadas (BEGIN IMMEDIATE),
então 'contar e inserir' é atômico mesmo com requisições simultâneas."""
import random

from sqlalchemy import Connection, text

from app import timeutil


def allow(db: Connection, bucket: str, key: str, limit: int, window_seconds: int) -> bool:
    """Registra a tentativa e retorna False se o limite da janela já foi atingido."""
    count = db.execute(
        text("SELECT count(*) FROM rate_limit_events WHERE bucket=:b AND key=:k AND created_at > :since"),
        {"b": bucket, "k": key, "since": timeutil.now(seconds=-window_seconds)},
    ).scalar_one()
    if count >= limit:
        db.commit()
        return False
    db.execute(text("INSERT INTO rate_limit_events (bucket, key, created_at) VALUES (:b, :k, :now)"),
               {"b": bucket, "k": key, "now": timeutil.now()})
    if random.random() < 0.01:  # nosec B311 - só sorteia quando limpar registros antigos; não é uso criptográfico
        db.execute(text("DELETE FROM rate_limit_events WHERE created_at < :old"), {"old": timeutil.now(days=-1)})
    db.commit()
    return True
