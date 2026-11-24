"""Cópia de segurança consistente do banco (funciona com o sistema LIGADO: usa a API de backup do SQLite).
  python -m scripts.backup                       # grava em backups/sindnorte-AAAAMMDD-HHMMSS.db
  python -m scripts.backup --destino D:\\copias   # outra pasta
  python -m scripts.backup --manter 14           # apaga cópias antigas, mantendo as 14 mais recentes
Restaurar: pare o servidor, copie o arquivo de backup por cima de data/sindnorte.db e inicie de novo.
Guarde as cópias fora deste computador (HD externo/nuvem privada): elas contêm dados pessoais."""
import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import get_settings


def caminho_banco() -> Path:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise SystemExit("Backup por este script só vale para SQLite (DATABASE_URL=sqlite:///...).")
    return Path(url.database)


def fazer_backup(destino: Path, manter: int = 0, agora: datetime | None = None) -> Path:
    origem = caminho_banco()
    if not origem.is_file():
        raise SystemExit(f"Banco não encontrado: {origem}")
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / f"sindnorte-{(agora or datetime.now()).strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(f"file:{origem.resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(arq))
    try:
        src.backup(dst)  # cópia consistente mesmo com escritas em andamento
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != "ok":
        arq.unlink(missing_ok=True)
        raise SystemExit(f"A cópia falhou na verificação de integridade ({ok}); nada foi mantido.")
    try:
        os.chmod(arq, 0o600)  # só o dono lê (no Windows é ignorado)
    except OSError:
        pass
    if manter > 0:
        for velho in sorted(destino.glob("sindnorte-*.db"))[:-manter]:
            velho.unlink(missing_ok=True)
    return arq


def main(argv=None, saida=print) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default="backups")
    ap.add_argument("--manter", type=int, default=0, help="mantém só as N cópias mais recentes (0 = não apaga nada)")
    a = ap.parse_args(argv)
    arq = fazer_backup(Path(a.destino), a.manter)
    saida(f"Cópia criada: {arq} ({arq.stat().st_size // 1024} KB), integridade verificada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
