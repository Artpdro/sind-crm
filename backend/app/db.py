import sqlite3
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import Engine, make_url

from app.config import get_settings
from app.textutil import distancia_m, sem_acento


@lru_cache
def get_engine() -> Engine:
    if sqlite3.sqlite_version_info < (3, 35):
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} é antigo demais; é preciso 3.35 ou superior.")
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("sqlite"):
        raise RuntimeError("Esta versão local usa SQLite (DATABASE_URL=sqlite:///./data/sindnorte.db).")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None  # transações controladas abaixo (BEGIN IMMEDIATE)
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")   # sem isso o SQLite ignora chaves estrangeiras
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
        dbapi_conn.create_function("unaccent", 1, sem_acento, deterministic=True)  # busca sem diferenciar acentos/maiúsculas
        dbapi_conn.create_function("distancia_m", 4, distancia_m, deterministic=True)

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        # Toda transação pega o "lock de escrita" logo no início: escritas simultâneas são
        # serializadas (sem corrida em "conta e insere", "último admin", token de uso único).
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def get_db() -> Iterator[Connection]:
    """Uma conexão por requisição. Quem escreve chama db.commit() explicitamente."""
    conn = get_engine().connect()
    try:
        yield conn
    finally:
        conn.close()  # rollback implícito se algo ficou pendente
