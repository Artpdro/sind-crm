"""Cria o arquivo do banco SQLite e as tabelas. Pode rodar mais de uma vez (não apaga nada).
  python -m scripts.init_db"""
from app.config import get_settings
from app.db import get_engine
from app.schema import init_db

if __name__ == "__main__":
    criado = init_db(get_engine())
    print(f"Banco criado em {get_settings().database_url}" if criado else "Banco já estava pronto; nada foi alterado.")
