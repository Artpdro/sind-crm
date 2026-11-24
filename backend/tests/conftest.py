import os
import tempfile

import pytest

# Ambiente de teste ANTES de importar a aplicação.
os.environ.update(APP_ENV="test", GEOCODER_AUTO="false", COOKIE_SECURE="false", ALLOWED_ORIGINS="http://localhost:5173",
                  FRONTEND_BASE_URL="http://localhost:5173")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import timeutil  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.schema import init_db  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

PASSWORD = "Senha-Forte-123"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture(autouse=True)
def _banco_novo(tmp_path):
    """Cada teste roda num arquivo SQLite novo e isolado (nada vaza entre testes)."""
    from app.modules.auth import mailer
    mailer.OUTBOX.clear()
    from app.modules.geocoding import service as gsvc
    gsvc.resetar_estatisticas()
    get_engine.cache_clear()
    get_settings().database_url = f"sqlite:///{tmp_path / 'teste.db'}"
    get_settings().cnefe_db = str(tmp_path / 'cnefe.db')
    get_settings().tiles_dir = str(tmp_path / 'tiles')
    get_settings().pdf_mapa_ruas = False  # testes nunca acessam a internet; os de mapa de ruas ligam e usam um servidor falso
    from app.modules.geocoding import cnefe as _cn
    _cn.limpar_cache()
    init_db(get_engine())
    yield
    get_engine().dispose()
    get_engine.cache_clear()


@pytest.fixture
def owner_engine():
    """Acesso direto ao banco (para preparar/inspecionar dados, fora da API)."""
    return get_engine()


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app, base_url="http://localhost:5173", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def make_user():
    def _make(username="ana", role="operador", password=PASSWORD, email=None, must_change=False, active=True):
        with get_engine().begin() as c:
            return c.execute(
                text("INSERT INTO users (username, email, role, password_hash, must_change_password, is_active) "
                     "VALUES (:u, :e, :r, :h, :m, :a) RETURNING id"),
                {"u": username, "e": email, "r": role, "h": hash_password(password), "m": int(must_change), "a": int(active)},
            ).scalar_one()
    return _make


@pytest.fixture
def login(client, make_user):
    """Cria usuário e autentica. Retorna (client, csrf_headers)."""
    def _login(username="ana", role="operador", **kw):
        make_user(username=username, role=role, **kw)
        r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD}, headers=ORIGIN)
        assert r.status_code == 200, r.text
        return client, {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}
    return _login
