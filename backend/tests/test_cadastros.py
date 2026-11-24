import pytest

from tests.conftest import ORIGIN, PASSWORD

TABELAS = ["situacoes", "categorias", "portes", "tipos-empresa"]


def test_situacoes_iniciais_vem_do_banco_e_nao_do_codigo(login):
    c, h = login()
    nomes = {x["nome"] for x in c.get("/api/cadastros/situacoes").json()}
    assert {"Ativa", "Inativa", "Em acompanhamento", "Visitada", "Contato realizado", "Sem contato", "Pendente"} <= nomes


@pytest.mark.parametrize("tabela", TABELAS)
def test_admin_cria_edita_desativa_e_exclui(login, tabela):
    c, h = login("adm", "admin")
    r = c.post(f"/api/cadastros/{tabela}", json={"nome": "Novo Item"}, headers=h)
    assert r.status_code == 201
    iid = r.json()["id"]
    assert c.patch(f"/api/cadastros/{tabela}/{iid}", json={"nome": "Renomeado"}, headers=h).json()["nome"] == "Renomeado"
    assert c.patch(f"/api/cadastros/{tabela}/{iid}", json={"ativa": False}, headers=h).json()["ativa"] is False
    assert "Renomeado" not in {x["nome"] for x in c.get(f"/api/cadastros/{tabela}").json()}           # some da lista comum
    assert "Renomeado" in {x["nome"] for x in c.get(f"/api/cadastros/{tabela}?todas=true").json()}    # admin ainda vê
    assert c.delete(f"/api/cadastros/{tabela}/{iid}", headers=h).status_code == 204
    assert c.delete(f"/api/cadastros/{tabela}/{iid}", headers=h).status_code == 404


@pytest.mark.parametrize("tabela", TABELAS)
def test_nao_admin_nao_altera_cadastros(login, tabela):
    c, h = login("oper", "operador")
    assert c.post(f"/api/cadastros/{tabela}", json={"nome": "X"}, headers=h).status_code == 403
    assert c.patch(f"/api/cadastros/{tabela}/1", json={"nome": "X"}, headers=h).status_code == 403
    assert c.delete(f"/api/cadastros/{tabela}/1", headers=h).status_code == 403
    assert c.get(f"/api/cadastros/{tabela}").status_code == 200


def test_nao_admin_nao_ve_inativos_mesmo_pedindo(login):
    adm, ha = login("adm", "admin")
    sid = {x["nome"]: x["id"] for x in adm.get("/api/cadastros/situacoes").json()}["Pendente"]
    adm.patch(f"/api/cadastros/situacoes/{sid}", json={"ativa": False}, headers=ha)
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.conftest import PASSWORD
    from sqlalchemy import text
    from app.db import get_engine
    from app.security.passwords import hash_password
    with get_engine().begin() as db:
        db.execute(text("INSERT INTO users (username, role, password_hash) VALUES ('op2','operador',:h)"), {"h": hash_password(PASSWORD)})
    o = TestClient(app, base_url="http://localhost:5173")
    r = o.post("/api/auth/login", json={"username": "op2", "password": PASSWORD}, headers=ORIGIN)
    assert "Pendente" not in {x["nome"] for x in o.get("/api/cadastros/situacoes?todas=true").json()}
    o.close()


def test_nome_duplicado_sem_diferenciar_maiusculas_409(login):
    c, h = login("adm", "admin")
    assert c.post("/api/cadastros/categorias", json={"nome": "Padaria"}, headers=h).status_code == 201
    assert c.post("/api/cadastros/categorias", json={"nome": "padaria"}, headers=h).status_code == 409


def test_excluir_item_em_uso_409_e_desativar_funciona(login):
    c, h = login("adm", "admin")
    cid = c.post("/api/cadastros/categorias", json={"nome": "Varejo"}, headers=h).json()["id"]
    c.post("/api/empresas", json={"nome": "E", "categoria_ids": [cid]}, headers=h)
    r = c.delete(f"/api/cadastros/categorias/{cid}", headers=h)
    assert r.status_code == 409 and "Desative" in r.json()["detail"]
    assert c.patch(f"/api/cadastros/categorias/{cid}", json={"ativa": False}, headers=h).status_code == 200
    sid = {x["nome"]: x["id"] for x in c.get("/api/cadastros/situacoes").json()}["Ativa"]
    c.post("/api/empresas", json={"nome": "F", "situacao_id": sid}, headers=h)
    assert c.delete(f"/api/cadastros/situacoes/{sid}", headers=h).status_code == 409


def test_validacao_de_entradas(login):
    c, h = login("adm", "admin")
    for corpo in [{"nome": ""}, {"nome": "x" * 81}, {"nome": "a\x00b"}, {"nome": "ok", "extra": 1}]:
        assert c.post("/api/cadastros/categorias", json=corpo, headers=h).status_code == 422
    assert c.post("/api/cadastros/categorias", json={"nome": "ok", "cor": "#ffffff"}, headers=h).status_code == 422  # cor só p/ situação
    assert c.post("/api/cadastros/situacoes", json={"nome": "Cor ruim", "cor": "vermelho"}, headers=h).status_code == 422
    assert c.post("/api/cadastros/situacoes", json={"nome": "Boa", "cor": "#aabbcc", "ordem": 9}, headers=h).status_code == 201
    assert c.get("/api/cadastros/tabela_users").status_code == 404
    assert c.get("/api/cadastros/users").status_code == 404


def test_injection_no_nome_do_cadastro_fica_como_texto(login, owner_engine):
    c, h = login("adm", "admin")
    from sqlalchemy import text
    r = c.post("/api/cadastros/categorias", json={"nome": "'); DROP TABLE users;--"}, headers=h)
    assert r.status_code == 201
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


def test_cadastro_gera_auditoria(login, owner_engine):
    c, h = login("adm", "admin")
    from sqlalchemy import text
    c.post("/api/cadastros/categorias", json={"nome": "Nova"}, headers=h)
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM audit_log WHERE action='cadastro_criado'")).scalar_one() == 1


# ───────── Campos personalizados (admin) ─────────
def test_campos_so_admin_mexe(login):
    c, h = login("oper", "operador")
    corpo = {"chave": "teste_x", "rotulo": "Teste", "tipo": "texto"}
    assert c.post("/api/campos", json=corpo, headers=h).status_code == 403
    assert c.patch("/api/campos/1", json={"rotulo": "x"}, headers=h).status_code == 403
    assert c.delete("/api/campos/1", headers=h).status_code == 403
    assert c.get("/api/campos").status_code == 200


def test_campo_validacoes(login):
    c, h = login("adm", "admin")
    base = {"chave": "origem", "rotulo": "Origem", "tipo": "selecao"}
    assert c.post("/api/campos", json=base, headers=h).status_code == 422                                   # sem opções
    assert c.post("/api/campos", json={**base, "opcoes": ["a", "a"]}, headers=h).status_code == 422          # repetidas
    assert c.post("/api/campos", json={**base, "tipo": "texto", "opcoes": ["a"]}, headers=h).status_code == 422
    for chave in ["Origem", "1abc", "a b", "a-b", "x"]:
        assert c.post("/api/campos", json={**base, "chave": chave, "opcoes": ["a"]}, headers=h).status_code == 422
    assert c.post("/api/campos", json={**base, "opcoes": ["a", "b"]}, headers=h).status_code == 201
    assert c.post("/api/campos", json={**base, "opcoes": ["a", "b"]}, headers=h).status_code == 409
    assert c.post("/api/campos", json={**base, "chave": "outro", "tipo": "texto", "id": 5}, headers=h).status_code == 422


def test_campo_tipo_e_chave_nao_mudam_e_campo_em_uso_nao_exclui(login):
    c, h = login("adm", "admin")
    cid = c.post("/api/campos", json={"chave": "obs", "rotulo": "Obs", "tipo": "texto"}, headers=h).json()["id"]
    assert c.patch(f"/api/campos/{cid}", json={"tipo": "numero"}, headers=h).status_code == 422
    assert c.patch(f"/api/campos/{cid}", json={"chave": "outra"}, headers=h).status_code == 422
    c.post("/api/empresas", json={"nome": "E", "campos": {"obs": "oi"}}, headers=h)
    assert c.delete(f"/api/campos/{cid}", headers=h).status_code == 409
    assert c.patch(f"/api/campos/{cid}", json={"ativo": False}, headers=h).status_code == 200
