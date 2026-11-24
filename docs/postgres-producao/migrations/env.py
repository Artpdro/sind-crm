import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ.get("MIGRATION_DATABASE_URL")
if not url:
    raise SystemExit("Defina MIGRATION_DATABASE_URL (usuário dono do schema, não o usuário da aplicação).")


def run() -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run()
