import threading

import pytest
from sqlalchemy import text

from tests.conftest import ORIGIN, PASSWORD

EMP = {"nome": "PADARIA DO ZE LTDA", "nome_fantasia": "Padaria do Zé", "cnpj": "12.884.495/0001-48"}


def _criar(c, h, **kw):
    return c.post("/api/empresas", json={**EMP, **kw}, headers=h)


def _cad(c, nome):
    return {x["nome"]: x["id"] for x in c.get(f"/api/cadastros/{nome}").json()}


# ───────── CNPJ ─────────
def test_cnpj_formatado_e_salvo_normalizado_como_texto(login, owner_engine):
    c, h = login()
    r = _criar(c, h)
    assert r.status_code == 201 and r.json()["cnpj"] == "12884495000148"
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT cnpj FROM empresas")).scalar_one() == "12884495000148"


def test_cnpj_zero_a_esquerda_preservado(login):
    c, h = login()
    assert _criar(c, h, cnpj="02.786.558/0021-13").json()["cnpj"] == "02786558002113"


@pytest.mark.parametrize("ruim", ["123", "12.884.495/0001-99", "11111111111111", "19882742491", "abc"])
def test_cnpj_invalido_422(login, ruim):
    c, h = login()
    assert _criar(c, h, cnpj=ruim).status_code == 422


def test_cnpj_duplicado_409_mesmo_com_formatacao_diferente(login):
    c, h = login()
    primeiro = _criar(c, h).json()
    r = _criar(c, h, nome="OUTRA", cnpj="12884495000148")
    assert r.status_code == 409 and r.json()["empresa_id"] == primeiro["id"]


def test_empresas_sem_cnpj_podem_coexistir(login):
    c, h = login()
    assert _criar(c, h, nome="A", cnpj=None).status_code == 201
    assert _criar(c, h, nome="B", cnpj="").status_code == 201


def test_corrida_criacao_simultanea_mesmo_cnpj_so_uma_vence(login, make_user):
    from fastapi.testclient import TestClient
    from app.main import app
    c1, h1 = login("user1", "operador")
    make_user("user2", "operador")
    c2 = TestClient(app, base_url="http://localhost:5173")
    r2 = c2.post("/api/auth/login", json={"username": "user2", "password": PASSWORD}, headers=ORIGIN)
    h2 = {**ORIGIN, "X-CSRF-Token": r2.json()["csrf_token"]}
    res = []
    ts = [threading.Thread(target=lambda c=c, h=h: res.append(_criar(c, h).status_code)) for c, h in ((c1, h1), (c2, h2)) * 3]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(res) == [201] + [409] * 5
    c2.close()


# ───────── Consulta por CNPJ ─────────
def test_consulta_cnpj_encontrada_com_qualquer_formatacao(login):
    c, h = login()
    eid = _criar(c, h, cidade="Paulista").json()["id"]
    for forma in ["12884495000148", "12.884.495/0001-48", "12 884 495 0001 48"]:
        r = c.get(f"/api/empresas/cnpj/{forma}")
        assert r.status_code == 200 and r.json()["encontrada"] and r.json()["empresa"]["id"] == eid


def test_consulta_cnpj_nao_encontrada(login):
    c, h = login()
    r = c.get("/api/empresas/cnpj/12884495000148")
    assert r.status_code == 404 and r.json()["detail"] == "Empresa não encontrada." and r.json()["encontrada"] is False


def test_consulta_cnpj_invalido_e_injection(login):
    c, h = login()
    for ruim in ["123", "' OR 1=1 --", "x" * 50]:
        assert c.get(f"/api/empresas/cnpj/{ruim}").status_code == 422


def test_consulta_cnpj_exige_login_e_tem_rate_limit(client, login):
    assert client.get("/api/empresas/cnpj/12884495000148").status_code == 401
    c, h = login()
    codes = [c.get("/api/empresas/cnpj/12884495000148").status_code for _ in range(125)]
    assert 429 in codes


def test_consulta_cnpj_de_empresa_excluida_nao_acha(login):
    c, h = login("adm", "admin")
    eid = _criar(c, h).json()["id"]
    assert c.delete(f"/api/empresas/{eid}", headers=h).status_code == 204
    assert c.get("/api/empresas/cnpj/12884495000148").status_code == 404


# ───────── Dados ─────────
def test_numero_do_endereco_aceita_texto_livre(login):
    c, h = login()
    for n in ["10", "10A", "10-B", "100/102", "S/N", "Loja 01", "Fundos"]:
        r = _criar(c, h, nome=f"E{n}", cnpj=None, numero=n)
        assert r.status_code == 201 and r.json()["numero"] == n


def test_numero_base_extraido_para_filtro_par_impar(login, owner_engine):
    c, h = login()
    for n in ["10A", "S/N", "Loja 01"]:
        _criar(c, h, nome=f"E{n}", cnpj=None, numero=n)
    with owner_engine.begin() as db:
        got = dict(db.execute(text("SELECT numero, numero_base FROM empresas")).all())
    assert got == {"10A": 10, "S/N": None, "Loja 01": 1}


def test_telefone_e_cep_texto_com_zeros(login):
    c, h = login()
    r = _criar(c, h, celular="(81) 99558-3225", fone="0800 123 4567", cep="01001-000").json()
    assert r["cep"] == "01001000" and r["celular"] == "81995583225" and r["fone"] == "08001234567"
    assert all(isinstance(r[k], str) for k in ("cep", "celular", "fone"))


def test_emails_validados_e_emails_adicionais(login):
    c, h = login()
    assert _criar(c, h, email="nao-e-email").status_code == 422
    r = _criar(c, h, email="a@b.com", emails_adicionais=["c@d.com", "e@f.com"])
    assert r.status_code == 201 and r.json()["emails_adicionais"] == ["c@d.com", "e@f.com"]
    assert _criar(c, h, nome="X", cnpj=None, emails_adicionais=["ruim"]).status_code == 422


def test_campos_obrigatorios_e_limites(login):
    c, h = login()
    assert c.post("/api/empresas", json={"cnpj": None}, headers=h).status_code == 422
    assert _criar(c, h, nome="   ").status_code == 422
    assert _criar(c, h, nome="x" * 301).status_code == 422
    assert _criar(c, h, nome="Y", cnpj=None, informacoes_adicionais="z" * 20001).status_code == 422
    assert _criar(c, h, nome="Y", cnpj=None, estado="PERNAMBUCO").status_code == 422
    assert _criar(c, h, nome="Y", cnpj=None, estado="pe").json()["estado"] == "PE"


# ───────── Segurança: mass assignment, IDOR, RBAC ─────────
@pytest.mark.parametrize("campo,valor", [("id", 999), ("latitude", 1.0), ("longitude", 2.0), ("geocode_status", "localizado"),
                                         ("created_by", 1), ("version", 50), ("deleted_at", "x"), ("numero_base", 1)])
def test_mass_assignment_rejeitado_na_criacao(login, campo, valor):
    c, h = login()
    assert _criar(c, h, **{campo: valor}).status_code == 422


def test_mass_assignment_rejeitado_na_edicao(login):
    c, h = login()
    e = _criar(c, h).json()
    for campo in ["id", "latitude", "geocode_status", "created_by", "deleted_at"]:
        r = c.patch(f"/api/empresas/{e['id']}", json={"version": e["version"], campo: 1}, headers=h)
        assert r.status_code == 422


def test_visualizador_le_mas_nao_escreve(login, make_user):
    adm, ha = login("adm", "admin")
    e = _criar(adm, ha).json()
    from fastapi.testclient import TestClient
    from app.main import app
    make_user("viewer", "visualizador")
    v = TestClient(app, base_url="http://localhost:5173")
    rv = v.post("/api/auth/login", json={"username": "viewer", "password": PASSWORD}, headers=ORIGIN)
    hv = {**ORIGIN, "X-CSRF-Token": rv.json()["csrf_token"]}
    assert v.get(f"/api/empresas/{e['id']}").status_code == 200
    assert v.get("/api/empresas").status_code == 200
    assert v.post("/api/empresas", json=EMP, headers=hv).status_code == 403
    assert v.patch(f"/api/empresas/{e['id']}", json={"version": 1, "nome": "X"}, headers=hv).status_code == 403
    assert v.delete(f"/api/empresas/{e['id']}", headers=hv).status_code == 403
    assert v.post(f"/api/empresas/{e['id']}/interacoes", json={"tipo": "visita", "descricao": "x"}, headers=hv).status_code == 403
    v.close()


def test_operador_nao_exclui_so_admin(login):
    c, h = login("oper", "operador")
    e = _criar(c, h).json()
    assert c.delete(f"/api/empresas/{e['id']}", headers=h).status_code == 403
    assert c.get(f"/api/empresas/{e['id']}").status_code == 200


def test_sem_login_tudo_401(client):
    for m, p in [("get", "/api/empresas"), ("get", "/api/empresas/1"), ("get", "/api/empresas/1/interacoes"),
                 ("get", "/api/cadastros/situacoes"), ("get", "/api/campos")]:
        assert getattr(client, m)(p).status_code == 401


def test_escrita_sem_csrf_403(login):
    c, h = login()
    assert c.post("/api/empresas", json=EMP, headers=ORIGIN).status_code == 403


def test_ids_inexistentes_ou_invalidos(login):
    c, h = login()
    assert c.get("/api/empresas/99999").status_code == 404
    assert c.get("/api/empresas/abc").status_code == 422
    assert c.patch("/api/empresas/99999", json={"version": 1, "nome": "x"}, headers=h).status_code == 404
    assert c.get("/api/empresas/99999/interacoes").status_code == 404
    assert c.post("/api/empresas/99999/interacoes", json={"tipo": "visita", "descricao": "x"}, headers=h).status_code == 404


def test_relacionamentos_invalidos_ou_inativos_422(login):
    c, h = login("adm", "admin")
    assert _criar(c, h, situacao_id=99999).status_code == 422
    assert _criar(c, h, cnpj=None, porte_id=99999).status_code == 422
    assert _criar(c, h, cnpj=None, categoria_ids=[99999]).status_code == 422
    sid = _cad(c, "situacoes")["Ativa"]
    c.patch(f"/api/cadastros/situacoes/{sid}", json={"ativa": False}, headers=h)
    assert _criar(c, h, cnpj=None, situacao_id=sid).status_code == 422


# ───────── Edição, bloqueio otimista, geolocalização ─────────
def test_edicao_parcial_preserva_o_resto_e_incrementa_versao(login):
    c, h = login()
    e = _criar(c, h, cidade="Paulista", bairro="Centro").json()
    r = c.patch(f"/api/empresas/{e['id']}", json={"version": e["version"], "bairro": "Nobre"}, headers=h).json()
    assert r["bairro"] == "Nobre" and r["cidade"] == "Paulista" and r["nome"] == EMP["nome"] and r["version"] == e["version"] + 1


def test_bloqueio_otimista_409_na_versao_velha(login):
    c, h = login()
    e = _criar(c, h).json()
    assert c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "nome": "NOVO"}, headers=h).status_code == 200
    assert c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "nome": "VELHO"}, headers=h).status_code == 409
    assert c.get(f"/api/empresas/{e['id']}").json()["nome"] == "NOVO"


def test_nome_nao_pode_ser_apagado_na_edicao(login):
    c, h = login()
    e = _criar(c, h).json()
    assert c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "nome": None}, headers=h).status_code == 422


def test_cnpj_na_edicao_nao_pode_colidir_com_outra(login):
    c, h = login()
    _criar(c, h)
    b = _criar(c, h, nome="B", cnpj="02.786.558/0021-13").json()
    assert c.patch(f"/api/empresas/{b['id']}", json={"version": 1, "cnpj": "12884495000148"}, headers=h).status_code == 409


def test_mudar_endereco_marca_geocodificacao_desatualizada_e_preserva_coordenadas(login, owner_engine):
    c, h = login()
    e = _criar(c, h, rua="Rua A", numero="10").json()
    with owner_engine.begin() as db:
        db.execute(text("UPDATE empresas SET latitude=-7.94, longitude=-34.87, geocode_status='localizado' WHERE id=:i"), {"i": e["id"]})
    v = c.get(f"/api/empresas/{e['id']}").json()["version"]
    r = c.patch(f"/api/empresas/{e['id']}", json={"version": v, "rua": "Rua B"}, headers=h).json()
    assert r["geocode_desatualizada"] is True and (r["latitude"], r["longitude"]) == (-7.94, -34.87)
    assert r["geocode_status"] == "pendente"      # volta para a fila de geocodificação; o marcador antigo continua no mapa até haver outro válido


def test_mudar_campo_que_nao_e_endereco_nao_marca_desatualizada(login, owner_engine):
    c, h = login()
    e = _criar(c, h, rua="Rua A").json()
    with owner_engine.begin() as db:
        db.execute(text("UPDATE empresas SET latitude=-7.94, longitude=-34.87, geocode_status='localizado' WHERE id=:i"), {"i": e["id"]})
    r = c.patch(f"/api/empresas/{e['id']}", json={"version": 2 - 1, "email": "x@y.com"}, headers=h).json()
    assert r["geocode_desatualizada"] is False


def test_empresa_nova_nasce_com_geocodificacao_pendente(login):
    c, h = login()
    r = _criar(c, h, rua="Rua A").json()
    assert r["geocode_status"] == "pendente" and r["latitude"] is None


# ───────── Exclusão lógica ─────────
def test_exclusao_logica_some_das_telas_mas_historico_fica(login, owner_engine):
    c, h = login("adm", "admin")
    e = _criar(c, h).json()
    c.post(f"/api/empresas/{e['id']}/interacoes", json={"tipo": "visita", "descricao": "visitei"}, headers=h)
    assert c.delete(f"/api/empresas/{e['id']}", headers=h).status_code == 204
    assert c.get(f"/api/empresas/{e['id']}").status_code == 404
    assert c.get("/api/empresas").json()["total"] == 0
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM interacoes")).scalar_one() == 1
        assert db.execute(text("SELECT deleted_at IS NOT NULL FROM empresas")).scalar_one() == 1


# ───────── Histórico de interações ─────────
def test_interacao_registra_usuario_da_sessao_e_preserva_historico(login):
    c, h = login("oper", "operador")
    e = _criar(c, h).json()
    sit = _cad(c, "situacoes")
    r = c.post(f"/api/empresas/{e['id']}/interacoes", headers=h,
               json={"tipo": "visita", "descricao": "Apresentação dos serviços", "situacao_resultante_id": sit["Em acompanhamento"]})
    assert r.status_code == 201
    c.post(f"/api/empresas/{e['id']}/interacoes", headers=h,
           json={"tipo": "ligacao", "descricao": "Retorno", "situacao_resultante_id": sit["Contato realizado"]})
    hist = c.get(f"/api/empresas/{e['id']}/interacoes").json()
    assert hist["total"] == 2 and all(i["usuario"] == "oper" for i in hist["itens"])
    assert hist["itens"][0]["descricao"] == "Retorno" and hist["itens"][1]["situacao_resultante"] == "Em acompanhamento"
    assert c.get(f"/api/empresas/{e['id']}").json()["situacao_nome"] == "Contato realizado"


def test_interacao_nao_aceita_usuario_no_corpo(login):
    c, h = login()
    e = _criar(c, h).json()
    r = c.post(f"/api/empresas/{e['id']}/interacoes", json={"tipo": "visita", "descricao": "x", "usuario_id": 1}, headers=h)
    assert r.status_code == 422


def test_interacao_valida_tipo_e_tamanho(login):
    c, h = login()
    e = _criar(c, h).json()
    for corpo in [{"tipo": "teletransporte", "descricao": "x"}, {"tipo": "visita", "descricao": ""},
                  {"tipo": "visita", "descricao": "x" * 5001}, {"tipo": "visita", "descricao": "x", "situacao_resultante_id": 99999}]:
        assert c.post(f"/api/empresas/{e['id']}/interacoes", json=corpo, headers=h).status_code == 422


def test_historico_nao_pode_ser_editado_nem_apagado_pela_api(login):
    c, h = login("adm", "admin")
    e = _criar(c, h).json()
    iid = c.post(f"/api/empresas/{e['id']}/interacoes", json={"tipo": "outro", "descricao": "x"}, headers=h).json()["id"]
    for m in ("patch", "put", "delete"):
        assert getattr(c, m)(f"/api/empresas/{e['id']}/interacoes/{iid}", headers=h).status_code in (404, 405)
    assert c.get(f"/api/empresas/{e['id']}/interacoes").json()["total"] == 1


# ───────── Categorias e campos personalizados na empresa ─────────
def test_categorias_multiplas_na_empresa(login):
    c, h = login("adm", "admin")
    a = c.post("/api/cadastros/categorias", json={"nome": "Alimentação"}, headers=h).json()["id"]
    b = c.post("/api/cadastros/categorias", json={"nome": "Varejo"}, headers=h).json()["id"]
    e = _criar(c, h, categoria_ids=[a, b]).json()
    assert [x["nome"] for x in e["categorias"]] == ["Alimentação", "Varejo"]
    e2 = c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "categoria_ids": [b]}, headers=h).json()
    assert [x["nome"] for x in e2["categorias"]] == ["Varejo"]
    assert c.get(f"/api/empresas?categoria_id={a}").json()["total"] == 0
    assert c.get(f"/api/empresas?categoria_id={b}").json()["total"] == 1
    assert _criar(c, h, cnpj=None, categoria_ids=[a, a]).status_code == 422


def test_campos_personalizados_por_tipo(login):
    c, h = login("adm", "admin")
    for corpo in [{"chave": "interesse_repis", "rotulo": "Possui interesse em REPIS?", "tipo": "booleano"},
                  {"chave": "data_visita", "rotulo": "Data da visita", "tipo": "data"},
                  {"chave": "obs_comercial", "rotulo": "Observação comercial", "tipo": "texto"},
                  {"chave": "funcionarios", "rotulo": "Funcionários", "tipo": "numero"},
                  {"chave": "origem", "rotulo": "Origem", "tipo": "selecao", "opcoes": ["Indicação", "Evento"]}]:
        assert c.post("/api/campos", json=corpo, headers=h).status_code == 201
    ok = {"interesse_repis": True, "data_visita": "2026-09-27", "obs_comercial": "ligar depois", "funcionarios": 12, "origem": "Evento"}
    e = _criar(c, h, campos=ok).json()
    assert {x["chave"]: x["valor"] for x in e["campos"]} == ok
    ruins = [{"interesse_repis": "sim"}, {"data_visita": "27/09/2026"}, {"data_visita": "2026-13-45"}, {"funcionarios": "doze"},
             {"funcionarios": True}, {"origem": "Outra"}, {"inexistente": 1}, {"obs_comercial": "x" * 5001}]
    for campos in ruins:
        assert _criar(c, h, cnpj=None, campos=campos).status_code == 422, campos
    r = c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "campos": {"interesse_repis": None}}, headers=h).json()
    assert {x["chave"]: x["valor"] for x in r["campos"]}["interesse_repis"] is None


def test_campo_obrigatorio_e_desativado(login):
    c, h = login("adm", "admin")
    c.post("/api/campos", json={"chave": "regiao", "rotulo": "Região", "tipo": "texto", "obrigatorio": True}, headers=h)
    assert _criar(c, h, cnpj=None).status_code == 422
    assert _criar(c, h, cnpj=None, campos={"regiao": "Norte"}).status_code == 201
    cid = c.get("/api/campos").json()[0]["id"]
    c.patch(f"/api/campos/{cid}", json={"ativo": False}, headers=h)
    assert _criar(c, h, cnpj=None, nome="Z").status_code == 201  # campo inativo não é mais exigido


# ───────── Listagem, filtros, paginação, injeção, XSS ─────────
def test_listagem_paginada_e_filtros_combinados(login):
    c, h = login()
    for i in range(30):
        _criar(c, h, nome=f"Empresa {i:02d}", cnpj=None, cidade="Paulista" if i % 2 else "Olinda", bairro="Centro" if i < 10 else "Nobre")
    r = c.get("/api/empresas?page_size=10").json()
    assert r["total"] == 30 and len(r["itens"]) == 10 and r["itens"][0]["nome"] == "Empresa 00"
    assert len(c.get("/api/empresas?page=3&page_size=10").json()["itens"]) == 10
    assert c.get("/api/empresas?cidade=paulista").json()["total"] == 15
    assert c.get("/api/empresas?cidade=Paulista&bairro=Centro").json()["total"] == 5
    assert c.get("/api/empresas?q=empresa 07").json()["total"] == 1


def test_busca_por_cnpj_parcial_e_nome_fantasia(login):
    c, h = login()
    _criar(c, h)
    assert c.get("/api/empresas?q=12.884.495").json()["total"] == 1
    assert c.get("/api/empresas?q=12884").json()["total"] == 1
    assert c.get("/api/empresas?q=Padaria do").json()["total"] == 1


def test_paginacao_abusiva_rejeitada(login):
    c, h = login()
    for q in ["page_size=1000", "page_size=0", "page=0", "page=-1", "page=abc", "q=" + "a" * 101]:
        assert c.get(f"/api/empresas?{q}").status_code == 422


def test_sql_injection_nos_filtros_nao_funciona(login, owner_engine):
    c, h = login()
    _criar(c, h)
    for payload in ["' OR '1'='1", "'; DROP TABLE empresas;--", "%' OR 1=1 --", "\" OR \"\"=\"", "x' UNION SELECT password_hash FROM users--"]:
        for par in ("q", "cidade", "bairro"):
            r = c.get("/api/empresas", params={par: payload})
            assert r.status_code == 200 and r.json()["total"] == 0, (par, payload)
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas")).scalar_one() == 1


def test_coringas_like_sao_tratados_como_texto(login):
    c, h = login()
    _criar(c, h, nome="Empresa 100% Boa", cnpj=None)
    _criar(c, h, nome="Empresa Comum", cnpj=None)
    assert c.get("/api/empresas", params={"q": "%"}).json()["total"] == 1
    assert c.get("/api/empresas", params={"q": "_"}).json()["total"] == 0


def test_xss_armazenado_volta_como_texto_json_seguro(login):
    c, h = login()
    xss = '<img src=x onerror=alert(1)><script>alert(2)</script>'
    e = _criar(c, h, nome=xss, nome_fantasia=xss, informacoes_adicionais=xss, cnpj=None).json()
    r = c.get(f"/api/empresas/{e['id']}")
    assert r.json()["nome"] == xss  # guardado como texto; a tela escapa (React) e o PDF usará autoescape
    assert r.headers["content-type"].startswith("application/json") and r.headers["x-content-type-options"] == "nosniff"
    c.post(f"/api/empresas/{e['id']}/interacoes", json={"tipo": "outro", "descricao": xss}, headers=h)
    assert c.get(f"/api/empresas/{e['id']}/interacoes").json()["itens"][0]["descricao"] == xss


def test_auditoria_nao_grava_dados_pessoais_das_empresas(login, owner_engine):
    c, h = login()
    e = _criar(c, h, email="segredo@empresa.com", celular="81999998888").json()
    c.patch(f"/api/empresas/{e['id']}", json={"version": 1, "email": "novo@empresa.com", "rua": "Rua Secreta 77"}, headers=h)
    with owner_engine.begin() as db:
        dump = " ".join(str(tuple(r)) for r in db.execute(text("SELECT * FROM audit_log")).all())
        acoes = {r[0] for r in db.execute(text("SELECT action FROM audit_log"))}
    assert {"empresa_criada", "empresa_editada"} <= acoes
    for pii in ["segredo@empresa.com", "novo@empresa.com", "81999998888", "Rua Secreta", EMP["nome"], "12884495000148"]:
        assert pii not in dump
    assert "email" in dump  # mas registra QUAIS campos mudaram
