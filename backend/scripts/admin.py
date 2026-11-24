"""CLI administrativa (rodar no servidor/máquina do admin, nunca exposta na web).
  python -m scripts.admin create-admin USERNAME [--email E]
  python -m scripts.admin reset-password USERNAME
A senha é lida de forma interativa (getpass) ou da variável ADMIN_PASSWORD; nunca vai na linha de comando."""
import argparse
import getpass
import os
import sys

from sqlalchemy import text

from app import timeutil
from app.db import get_engine
from app.schema import init_db
from app.security.passwords import hash_password, validate_password_policy


def _password(username: str) -> str:
    pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Nova senha: ")
    err = validate_password_policy(pw, username)
    if err:
        sys.exit(err)
    return pw


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin"); c.add_argument("username"); c.add_argument("--email")
    r = sub.add_parser("reset-password"); r.add_argument("username")
    a = ap.parse_args()
    init_db(get_engine())
    with get_engine().begin() as db:
        if a.cmd == "create-admin":
            db.execute(text("INSERT INTO users (username, email, role, password_hash, must_change_password) "
                            "VALUES (:u, :e, 'admin', :h, 0)"),
                       {"u": a.username, "e": a.email, "h": hash_password(_password(a.username))})
            db.execute(text("INSERT INTO audit_log (username, action, details) VALUES (:u, 'admin_criado_via_cli', '{}')"), {"u": a.username})
            print("Administrador criado.")
        else:
            n = db.execute(text("UPDATE users SET password_hash=:h, must_change_password=1, failed_attempts=0, locked_until=NULL "
                                "WHERE username=:u"), {"u": a.username, "h": hash_password(_password(a.username))}).rowcount
            if not n:
                sys.exit("Usuário não encontrado.")
            db.execute(text("UPDATE sessions SET revoked_at=:now WHERE user_id=(SELECT id FROM users WHERE username=:u) AND revoked_at IS NULL"),
                       {"u": a.username, "now": timeutil.now()})
            db.execute(text("INSERT INTO audit_log (username, action, details) VALUES (:u, 'senha_resetada_via_cli', '{}')"), {"u": a.username})
            print("Senha redefinida; o usuário precisará trocá-la no próximo login.")


if __name__ == "__main__":
    main()
