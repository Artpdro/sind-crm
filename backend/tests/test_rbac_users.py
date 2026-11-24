import pytest
from sqlalchemy import text

from tests.conftest import ORIGIN, PASSWORD

NOVO = {"username": "novo", "role": "visualizador", "password": "Senha-Nova-123!"}


@pytest.mark.parametrize("role", ["operador", "visualizador"])
def test_nao_admin_nao_lista_nem_cria_usuarios(login, role):
    client, h = login(role=role)
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/users", json=NOVO, headers=h).status_code == 403
    assert client.patch("/api/users/1", json={"role": "admin"}, headers=h).status_code == 403


def test_escalada_vertical_operador_nao_vira_admin(login, make_user, owner_engine):
    client, h = login("oper", "operador")
    uid = client.get("/api/auth/me").json()["user"]["id"]
    assert client.patch(f"/api/users/{uid}", json={"role": "admin"}, headers=h).status_code == 403
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT role FROM users WHERE id=:i"), {"i": uid}).scalar_one() == "operador"


def test_admin_cria_lista_e_novo_usuario_precisa_trocar_senha(login):
    client, h = login("adm", "admin")
    r = client.post("/api/users", json=NOVO, headers=h)
    assert r.status_code == 201 and r.json()["must_change_password"] is True
    assert NOVO["password"] not in r.text and "password_hash" not in r.text and "argon2" not in r.text
    assert {u["username"] for u in client.get("/api/users").json()} == {"adm", "novo"}


def test_admin_cria_usuario_com_campo_extra_rejeitado(login):
    client, h = login("adm", "admin")
    r = client.post("/api/users", json={**NOVO, "is_active": True, "must_change_password": False}, headers=h)
    assert r.status_code == 422


def test_criacao_valida_papel_senha_e_duplicidade(login):
    client, h = login("adm", "admin")
    assert client.post("/api/users", json={**NOVO, "role": "root"}, headers=h).status_code == 422
    assert client.post("/api/users", json={**NOVO, "password": "fraca"}, headers=h).status_code == 422
    assert client.post("/api/users", json={**NOVO, "username": "a b"}, headers=h).status_code == 422
    assert client.post("/api/users", json=NOVO, headers=h).status_code == 201
    assert client.post("/api/users", json={**NOVO, "username": "NOVO"}, headers=h).status_code == 409  # citext


def test_sql_injection_em_username_e_login(client, make_user, owner_engine):
    make_user()
    for payload in ["' OR '1'='1", "ana'; DROP TABLE users;--", "ana' --", "\" OR \"\"=\""]:
        assert client.post("/api/auth/login", json={"username": payload, "password": "x" * 12}, headers=ORIGIN).status_code in (401, 429)
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


def test_nao_remove_ultimo_admin(login):
    client, h = login("adm", "admin")
    uid = client.get("/api/auth/me").json()["user"]["id"]
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=h).status_code == 409
    assert client.patch(f"/api/users/{uid}", json={"role": "operador"}, headers=h).status_code == 409


def test_rebaixar_usuario_derruba_suas_sessoes(login, make_user):
    admin, ha = login("adm", "admin")
    from fastapi.testclient import TestClient
    from app.main import app
    make_user("oper", "operador")
    with TestClient(app, base_url="http://localhost:5173") as op:
        assert op.post("/api/auth/login", json={"username": "oper", "password": PASSWORD}, headers=ORIGIN).status_code == 200
        uid = next(u["id"] for u in admin.get("/api/users").json() if u["username"] == "oper")
        assert admin.patch(f"/api/users/{uid}", json={"is_active": False}, headers=ha).status_code == 200
        assert op.get("/api/auth/me").status_code == 401


def test_race_dois_admins_se_rebaixando_ao_mesmo_tempo(login, make_user, owner_engine):
    """Concorrência: nunca pode sobrar zero administradores."""
    import threading
    from fastapi.testclient import TestClient
    from app.main import app
    a, ha = login("adm1", "admin")
    make_user("adm2", "admin")
    b = TestClient(app, base_url="http://localhost:5173")
    rb = b.post("/api/auth/login", json={"username": "adm2", "password": PASSWORD}, headers=ORIGIN)
    hb = {**ORIGIN, "X-CSRF-Token": rb.json()["csrf_token"]}
    ids = {u["username"]: u["id"] for u in a.get("/api/users").json()}
    res = []
    def go(cl, h, target):
        res.append(cl.patch(f"/api/users/{target}", json={"role": "visualizador"}, headers=h).status_code)
    ts = [threading.Thread(target=go, args=(a, ha, ids["adm2"])), threading.Thread(target=go, args=(b, hb, ids["adm1"]))]
    [t.start() for t in ts]; [t.join() for t in ts]
    with owner_engine.begin() as c:
        admins = c.execute(text("SELECT count(*) FROM users WHERE role='admin' AND is_active")).scalar_one()
    assert admins >= 1  # invariante: nunca zero administradores
    b.close()


def test_troca_de_papel_gera_auditoria(login, make_user, owner_engine):
    client, h = login("adm", "admin")
    make_user("oper", "operador")
    uid = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "oper")
    client.patch(f"/api/users/{uid}", json={"role": "visualizador"}, headers=h)
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action='permissao_alterada' AND entity_id=:i"), {"i": uid}).scalar_one() == 1


def test_usuario_inexistente_404_e_id_invalido_422(login):
    client, h = login("adm", "admin")
    assert client.patch("/api/users/99999", json={"role": "operador"}, headers=h).status_code == 404
    assert client.patch("/api/users/abc", json={"role": "operador"}, headers=h).status_code == 422
