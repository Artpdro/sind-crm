import json
import socket
import time

import pytest
from sqlalchemy import text

from app.modules.companies.normalizers import numero_base
from tests.conftest import ORIGIN, PASSWORD


def E(db, nome="E", numero="100", lat=-7.94, lon=-34.87, **kw):
    cols = {"nome": nome, "rua": "Rua Marcondes", "numero": numero, "numero_base": numero_base(numero), "bairro": "Centro",
            "cidade": "Paulista", "estado": "PE", "latitude": lat, "longitude": lon,
            "geocode_status": "localizado" if lat is not None else "pendente", **kw}
    return db.execute(text(f"INSERT INTO empresas ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) RETURNING id"), cols).scalar_one()  # noqa: S608


def mapa(c, **q):
    r = c.get("/api/mapa/empresas", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def ids(r):
    return sorted(m["id"] for m in r["marcadores"])


@pytest.fixture
def cli(login):
    return login("ana", "operador")[0]


# ───────── exemplos do pedido ─────────
@pytest.fixture
def cenario(owner_engine):
    with owner_engine.begin() as db:
        d = {}
        d["m100"] = E(db, "Marcondes 100", "100")
        d["m100a"] = E(db, "Marcondes 100A", "100A")
        d["m101"] = E(db, "Marcondes 101", "101")
        d["m150"] = E(db, "Marcondes 150", "150")
        d["m200"] = E(db, "Marcondes 200", "200")
        d["msn"] = E(db, "Marcondes S/N", "S/N")
        d["mfundos"] = E(db, "Marcondes Fundos", "Fundos")
        d["mloja2"] = E(db, "Marcondes Loja 2", "Loja 2")
        d["outra"] = E(db, "Outra Rua", "100", rua="Rua das Flores", lat=-7.95)
        d["nobre"] = E(db, "Nobre", "10", bairro="Nobre", rua="Av. Brasil", lat=-7.96)
        d["olinda"] = E(db, "Olinda 1", "5", cidade="Olinda", bairro="Carmo", rua="Rua do Sol", lat=-8.0, lon=-34.85)
        d["olinda2"] = E(db, "Olinda 2", "7", cidade="Olinda", bairro="Varadouro", rua="Rua do Sol", lat=-8.01, lon=-34.85)
        d["semloc"] = E(db, "Sem localização", "9", cidade="Olinda", bairro="Carmo", rua="Rua do Sol", lat=None, lon=None)
    return d


def test_exemplo1_rua_marcondes(cli, cenario):
    c = cenario
    r = mapa(cli, rua="Rua Marcondes")
    assert ids(r) == sorted(c[k] for k in ("m100", "m100a", "m101", "m150", "m200", "msn", "mfundos", "mloja2"))
    assert r["total"] == 8


def test_exemplo2_rua_e_numero(cli, cenario):
    c = cenario
    assert ids(mapa(cli, rua="Rua Marcondes", numero="100")) == sorted([c["m100"], c["m100a"]])   # 100 e 100A (mesmo número-base)
    assert ids(mapa(cli, rua="Rua Marcondes", numero="100A")) == [c["m100a"]]                      # texto exato
    assert ids(mapa(cli, rua="Rua Marcondes", numero="S/N")) == [c["msn"]]
    assert mapa(cli, rua="Rua Marcondes", numero="999")["total"] == 0


def test_exemplo3_bairro_e_cidade(cli, cenario):
    c = cenario
    assert c["m100"] in ids(mapa(cli, bairro="Centro", cidade="Paulista")) and c["nobre"] not in ids(mapa(cli, bairro="Centro", cidade="Paulista"))
    assert ids(mapa(cli, bairro="Nobre", cidade="Paulista")) == [c["nobre"]]
    assert mapa(cli, bairro="Centro", cidade="Olinda")["total"] == 0


def test_exemplo4_cidade(cli, cenario):
    c = cenario
    r = mapa(cli, cidade="Olinda")
    assert ids(r) == sorted([c["olinda"], c["olinda2"]]) and r["total"] == 3 and r["sem_localizacao"] == 1 and r["com_localizacao"] == 2


def test_exemplo5_sem_filtros_mostra_todas(cli, cenario):
    r = mapa(cli)
    assert r["total"] == 13 and r["com_localizacao"] == 12 and len(r["marcadores"]) == 12 and r["truncado"] is False


# ───────── pares e ímpares ─────────
def test_filtro_pares_e_impares(cli, cenario):
    c = cenario
    pares = mapa(cli, rua="Rua Marcondes", paridade="pares")
    assert ids(pares) == sorted([c["m100"], c["m100a"], c["m150"], c["m200"], c["mloja2"]])     # 100, 100A, 150, 200, Loja 2
    impares = mapa(cli, rua="Rua Marcondes", paridade="impares")
    assert ids(impares) == [c["m101"]]
    todos = mapa(cli, rua="Rua Marcondes", paridade="todos")
    assert todos["total"] == 8 and "excluidos_sem_numero" not in todos


def test_sem_numero_identificavel_nunca_e_classificado_como_par_ou_impar(cli, cenario):
    c = cenario
    for par in ("pares", "impares"):
        r = mapa(cli, rua="Rua Marcondes", paridade=par)
        assert c["msn"] not in ids(r) and c["mfundos"] not in ids(r)
        assert r["excluidos_sem_numero"] == 2                                                   # transparência: S/N e Fundos
    assert len(mapa(cli, rua="Rua Marcondes", paridade="pares")["marcadores"]) + len(mapa(cli, rua="Rua Marcondes", paridade="impares")["marcadores"]) + 2 == 8


def test_paridade_invalida_422(cli):
    assert cli.get("/api/mapa/empresas", params={"paridade": "todas"}).status_code == 422
    assert cli.get("/api/mapa/empresas", params={"paridade": "1 OR 1=1"}).status_code == 422


# ───────── busca sem acento / sem diferenciar maiúsculas / coringas ─────────
def test_busca_ignora_acentos_e_maiusculas(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "A", cidade="Cuiabá", bairro="São João", rua="R. São José", estado="MT")
        E(db, "B", cidade="Olinda")
    assert ids(mapa(cli, cidade="cuiaba")) == [a] and ids(mapa(cli, cidade="CUIABÁ")) == [a]
    assert ids(mapa(cli, bairro="sao joao")) == [a] and ids(mapa(cli, rua="sao jose")) == [a] and ids(mapa(cli, rua="SÃO JOSÉ")) == [a]
    assert mapa(cli, rua="sao")["total"] == 1 and mapa(cli, cidade="cuiab")["total"] == 0        # cidade/bairro: igualdade exata


def test_coringas_like_sao_texto(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "A", rua="Rua 100% Brasil")
        E(db, "B", rua="Rua Comum")
    assert ids(mapa(cli, rua="%")) == [a] and mapa(cli, rua="_")["total"] == 0 and mapa(cli, rua="100%")["total"] == 1
    assert mapa(cli, rua="\\")["total"] == 0


# ───────── filtros combinados e coerência com a lista ─────────
def _cats(db, nome):
    return db.execute(text("INSERT INTO categorias (nome) VALUES (:n) RETURNING id"), {"n": nome}).scalar_one()


def test_filtros_combinados_porte_tipo_situacao_categoria(cli, owner_engine):
    with owner_engine.begin() as db:
        sit = {r[0]: r[1] for r in db.execute(text("SELECT nome, id FROM situacoes"))}
        porte = {r[0]: r[1] for r in db.execute(text("SELECT nome, id FROM portes"))}
        tipo = {r[0]: r[1] for r in db.execute(text("SELECT nome, id FROM tipos_empresa"))}
        cat = _cats(db, "Alimentação")
        a = E(db, "A", situacao_id=sit["Em acompanhamento"], porte_id=porte["Pequena"], tipo_empresa_id=tipo["Matriz"])
        b = E(db, "B", situacao_id=sit["Em acompanhamento"], porte_id=porte["Grande"], tipo_empresa_id=tipo["Matriz"])
        c2 = E(db, "C", situacao_id=sit["Ativa"], porte_id=porte["Pequena"], tipo_empresa_id=tipo["Filial"])
        db.execute(text("INSERT INTO empresa_categorias VALUES (:e, :c)"), {"e": a, "c": cat})
    base = dict(cidade="Paulista", bairro="Centro")
    assert ids(mapa(cli, **base, porte_id=porte["Pequena"], situacao_id=sit["Em acompanhamento"])) == [a]
    assert ids(mapa(cli, **base, tipo_empresa_id=tipo["Matriz"])) == sorted([a, b])
    assert ids(mapa(cli, categoria_id=cat)) == [a]
    assert ids(mapa(cli, situacao_id=sit["Ativa"], porte_id=porte["Pequena"], tipo_empresa_id=tipo["Filial"])) == [c2]
    assert mapa(cli, situacao_id=sit["Ativa"], porte_id=porte["Grande"])["total"] == 0


def test_campos_personalizados_como_filtro(cli, owner_engine):
    with owner_engine.begin() as db:
        defs = {}
        for chave, tipo, ops in [("repis", "booleano", None), ("visita", "data", None), ("func", "numero", None),
                                 ("obs", "texto", None), ("origem", "selecao", '["Indicação","Evento"]')]:
            defs[chave] = db.execute(text("INSERT INTO campos_personalizados (chave, rotulo, tipo, opcoes) VALUES (:c,:c,:t,:o) RETURNING id"),
                                     {"c": chave, "t": tipo, "o": ops}).scalar_one()
        a, b = E(db, "A"), E(db, "B")
        for eid, vals in ((a, dict(repis=1, visita="2026-09-27", func=12, obs="Ligar na Terça", origem="Evento")),
                          (b, dict(repis=0, visita="2026-01-01", func=3, obs="nada", origem="Indicação"))):
            db.execute(text("INSERT INTO empresa_campos_valores (empresa_id,campo_id,valor_bool,valor_data,valor_numero,valor_texto) VALUES (:e,:c,:b,NULL,NULL,NULL)"),
                       {"e": eid, "c": defs["repis"], "b": vals["repis"]})
            db.execute(text("INSERT INTO empresa_campos_valores (empresa_id,campo_id,valor_data) VALUES (:e,:c,:v)"), {"e": eid, "c": defs["visita"], "v": vals["visita"]})
            db.execute(text("INSERT INTO empresa_campos_valores (empresa_id,campo_id,valor_numero) VALUES (:e,:c,:v)"), {"e": eid, "c": defs["func"], "v": vals["func"]})
            db.execute(text("INSERT INTO empresa_campos_valores (empresa_id,campo_id,valor_texto) VALUES (:e,:c,:v)"), {"e": eid, "c": defs["obs"], "v": vals["obs"]})
            db.execute(text("INSERT INTO empresa_campos_valores (empresa_id,campo_id,valor_texto) VALUES (:e,:c,:v)"), {"e": eid, "c": defs["origem"], "v": vals["origem"]})
    assert ids(mapa(cli, campo="repis:sim")) == [a] and ids(mapa(cli, campo="repis:não")) == [b] and ids(mapa(cli, campo="repis:false")) == [b]
    assert ids(mapa(cli, campo="visita:2026-09-27")) == [a] and ids(mapa(cli, campo="func:12")) == [a]
    assert ids(mapa(cli, campo="obs:terca")) == [a]                                    # texto: contém, sem acento
    assert ids(mapa(cli, campo="origem:Evento")) == [a] and mapa(cli, campo="origem:evento")["total"] == 0  # seleção: exata
    assert ids(mapa(cli, campo=["repis:sim", "origem:Evento"])) == [a] and mapa(cli, campo=["repis:sim", "origem:Indicação"])["total"] == 0


@pytest.mark.parametrize("campo", ["naoexiste:1", "semdoispontos", ":x", "repis:talvez", "visita:27/09/2026", "func:doze", "rep is:1", "x" * 300, "' OR 1=1 --:1"])
def test_filtro_de_campo_invalido_422(cli, owner_engine, campo):
    with owner_engine.begin() as db:
        for chave, tipo in (("repis", "booleano"), ("visita", "data"), ("func", "numero")):
            db.execute(text("INSERT INTO campos_personalizados (chave, rotulo, tipo) VALUES (:c,:c,:t)"), {"c": chave, "t": tipo})
    assert cli.get("/api/mapa/empresas", params={"campo": campo}).status_code == 422
    assert cli.get("/api/empresas", params={"campo": campo}).status_code == 422


def test_campo_inativo_ou_excesso_de_filtros_422(cli, owner_engine):
    with owner_engine.begin() as db:
        db.execute(text("INSERT INTO campos_personalizados (chave, rotulo, tipo, ativo) VALUES ('velho','v','texto',0)"))
        db.execute(text("INSERT INTO campos_personalizados (chave, rotulo, tipo) VALUES ('novo','n','texto')"))
    assert cli.get("/api/mapa/empresas", params={"campo": "velho:x"}).status_code == 422
    assert cli.get("/api/mapa/empresas", params={"campo": [f"novo:{i}" for i in range(11)]}).status_code == 422


def test_mapa_e_lista_mostram_exatamente_os_mesmos_resultados(cli, cenario):
    combos = [{}, {"rua": "Rua Marcondes"}, {"rua": "Rua Marcondes", "numero": "100"}, {"cidade": "Olinda"}, {"bairro": "Centro", "cidade": "Paulista"},
              {"rua": "Rua Marcondes", "paridade": "pares"}, {"rua": "marcondes", "paridade": "impares"}, {"q": "marcondes"}, {"cidade": "olinda", "rua": "sol"}]
    for q in combos:
        total_mapa = mapa(cli, **q)["total"]
        lista = cli.get("/api/empresas", params={**q, "page_size": 100}).json()
        assert lista["total"] == total_mapa == len(lista["itens"]), q
        com_loc = {i["id"] for i in lista["itens"] if i["latitude"] is not None}
        assert com_loc == set(ids(mapa(cli, **q))), q


# ───────── marcadores ─────────
def test_so_aparecem_empresas_localizadas_e_nao_excluidas(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "ok")
        E(db, "sem", lat=None, lon=None)
        E(db, "excluida", deleted_at="2026-01-01 00:00:00.000")
    r = mapa(cli)
    assert ids(r) == [a] and (r["total"], r["com_localizacao"], r["sem_localizacao"]) == (2, 1, 1)


def test_marcador_tem_so_o_minimo_e_cor_da_situacao(cli, owner_engine):
    with owner_engine.begin() as db:
        sid, cor = db.execute(text("SELECT id, cor FROM situacoes WHERE nome='Visitada'")).one()
        E(db, "SEGREDO LTDA", situacao_id=sid, cnpj="12884495000148", email="x@y.com", celular="81999990000")
        E(db, "R", geocode_status="revisao", geocode_desatualizada=1)
    r = mapa(cli)
    assert set(r["marcadores"][0]) == {"id", "lat", "lon", "cor", "revisao", "desatualizada", "aprox"}
    corpo = json.dumps(r)
    for dado in ("SEGREDO", "12884495000148", "x@y.com", "81999990000"):
        assert dado not in corpo
    assert r["marcadores"][0]["cor"] == cor and r["marcadores"][1]["revisao"] is True and r["marcadores"][1]["desatualizada"] is True


def test_limite_de_marcadores_marca_truncado(cli, owner_engine, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_marcadores", 5)
    with owner_engine.begin() as db:
        for n in range(8):
            E(db, f"E{n}", lat=-7.9 - n * 0.001)
    r = mapa(cli)
    assert r["truncado"] is True and len(r["marcadores"]) == 5 and r["total"] == 8 and r["com_localizacao"] == 8


def test_area_visivel_bbox(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "dentro", lat=-7.95, lon=-34.87)
        E(db, "fora", lat=-9.0, lon=-35.5)
    r = mapa(cli, sul=-8.0, norte=-7.9, oeste=-34.9, leste=-34.8)
    assert ids(r) == [a] and r["total"] == 2 and r["com_localizacao"] == 2     # contadores são da busca, não da tela


@pytest.mark.parametrize("q", [{"sul": -8}, {"sul": -7, "norte": -8, "oeste": -35, "leste": -34}, {"sul": -91, "norte": 0, "oeste": 0, "leste": 1},
                               {"sul": "x", "norte": 0, "oeste": 0, "leste": 1}, {"sul": "nan", "norte": 0, "oeste": 0, "leste": 1},
                               {"sul": -8, "norte": -7, "oeste": -34, "leste": -35}])
def test_area_invalida_422(cli, q):
    assert cli.get("/api/mapa/empresas", params=q).status_code == 422


def test_o_mapa_nunca_chama_o_geocodificador_nem_a_rede(cli, owner_engine, monkeypatch):
    from app.modules.geocoding import service as gsvc
    from app.modules.geocoding.client import Nominatim
    with owner_engine.begin() as db:
        a = E(db, "A")
        E(db, "B", lat=None, lon=None, geocode_status="pendente")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("o mapa tentou geocodificar/usar a rede!"))
    monkeypatch.setattr(gsvc, "criar_geocoder", boom)
    monkeypatch.setattr(gsvc, "processar_empresa", boom)
    monkeypatch.setattr(gsvc, "processar_lote", boom)
    monkeypatch.setattr(Nominatim, "buscar", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    for _ in range(3):
        assert mapa(cli)["com_localizacao"] == 1
    assert cli.get("/api/mapa/proximas", params={"lat": -7.94, "lon": -34.87}).status_code == 200
    assert cli.get(f"/api/mapa/empresas/{a}").status_code == 200
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM geocode_cache")).scalar_one() == 0
        assert db.execute(text("SELECT geocode_tentativas FROM empresas ORDER BY id")).scalars().all() == [0, 0]


def test_popup_do_marcador_e_o_minimo_necessario(cli, owner_engine):
    with owner_engine.begin() as db:
        sid = db.execute(text("SELECT id FROM situacoes WHERE nome='Ativa'")).scalar_one()
        pid = db.execute(text("SELECT id FROM portes WHERE nome='Pequena'")).scalar_one()
        cid = db.execute(text("INSERT INTO categorias (nome) VALUES ('Alimentação') RETURNING id")).scalar_one()
        i = E(db, "PADARIA", nome_fantasia="Padaria do Zé", cnpj="12884495000148", situacao_id=sid, porte_id=pid, email="x@y.com", celular="81999990000")
        db.execute(text("INSERT INTO empresa_categorias VALUES (:e,:c)"), {"e": i, "c": cid})
        gone = E(db, "excluida", deleted_at="2026-01-01 00:00:00.000")
    r = cli.get(f"/api/mapa/empresas/{i}").json()
    assert (r["nome"], r["nome_fantasia"], r["cnpj"], r["situacao_nome"], r["porte_nome"]) == ("PADARIA", "Padaria do Zé", "12884495000148", "Ativa", "Pequena")
    assert r["categorias"] == [{"id": cid, "nome": "Alimentação"}] and r["rua"] == "Rua Marcondes"
    assert "email" not in r and "celular" not in r and "fone" not in r
    assert cli.get(f"/api/mapa/empresas/{gone}").status_code == 404 and cli.get("/api/mapa/empresas/99999").status_code == 404
    assert cli.get("/api/mapa/empresas/abc").status_code == 422


# ───────── próximas ─────────
def test_proximas_ordenadas_por_distancia_e_dentro_do_raio(cli, owner_engine):
    with owner_engine.begin() as db:
        perto = E(db, "perto", lat=-7.9400, lon=-34.8700)
        medio = E(db, "medio", lat=-7.9440, lon=-34.8700)       # ≈ 445 m
        longe = E(db, "longe", lat=-7.9700, lon=-34.8700)       # ≈ 3,3 km
        E(db, "sem", lat=None, lon=None)
        E(db, "excl", lat=-7.9401, lon=-34.8700, deleted_at="2026-01-01 00:00:00.000")
    r = cli.get("/api/mapa/proximas", params={"lat": -7.94, "lon": -34.87, "raio_m": 1000}).json()
    assert [x["id"] for x in r["itens"]] == [perto, medio] and r["itens"][0]["distancia_m"] < 1 and 400 < r["itens"][1]["distancia_m"] < 500
    assert [x["id"] for x in cli.get("/api/mapa/proximas", params={"lat": -7.94, "lon": -34.87, "raio_m": 5000}).json()["itens"]] == [perto, medio, longe]
    assert len(cli.get("/api/mapa/proximas", params={"lat": -7.94, "lon": -34.87, "raio_m": 5000, "limite": 2}).json()["itens"]) == 2


def test_proximas_aceita_os_mesmos_filtros(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "a", lat=-7.9400, lon=-34.8700)
        E(db, "b", lat=-7.9401, lon=-34.8700, rua="Rua Outra")
    r = cli.get("/api/mapa/proximas", params={"lat": -7.94, "lon": -34.87, "rua": "Marcondes"}).json()
    assert [x["id"] for x in r["itens"]] == [a]


@pytest.mark.parametrize("q", [{"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": "x", "lon": 0}, {"lat": "nan", "lon": 0}, {"lat": 0, "lon": "inf"},
                               {"lat": 0, "lon": 0, "raio_m": 0}, {"lat": 0, "lon": 0, "raio_m": 20001}, {"lat": 0, "lon": 0, "limite": 201}, {"lon": 0}])
def test_proximas_parametros_invalidos_422(cli, q):
    assert cli.get("/api/mapa/proximas", params=q).status_code == 422


def test_proximas_tem_limite_de_consultas(cli):
    codes = [cli.get("/api/mapa/proximas", params={"lat": -7.9, "lon": -34.8}).status_code for _ in range(125)]
    assert 429 in codes


# ───────── segurança ─────────
def test_sem_login_401_e_visualizador_pode_ler(client, login, owner_engine):
    for p in ("/api/mapa/empresas", "/api/mapa/empresas/1", "/api/mapa/proximas?lat=0&lon=0"):
        assert client.get(p).status_code == 401
    c, h = login("viewer", "visualizador")
    assert c.get("/api/mapa/empresas").status_code == 200


def test_sql_injection_em_todos_os_parametros(cli, cenario, owner_engine):
    lixo = ["' OR '1'='1", "'; DROP TABLE empresas;--", "%' OR 1=1 --", "\" OR \"\"=\"", "x' UNION SELECT password_hash FROM users--", "1; DELETE FROM users"]
    for v in lixo:
        for par in ("rua", "bairro", "cidade", "numero", "q"):
            r = cli.get("/api/mapa/empresas", params={par: v})
            assert r.status_code == 200 and r.json()["total"] == 0, (par, v)
        for par in ("situacao_id", "porte_id", "categoria_id", "sul"):
            assert cli.get("/api/mapa/empresas", params={par: v}).status_code == 422
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas")).scalar_one() == 13 and db.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


def test_escrita_no_mapa_nao_existe(cli):
    h = {**ORIGIN}
    for m in ("post", "put", "patch", "delete"):
        assert getattr(cli, m)("/api/mapa/empresas", headers=h).status_code in (403, 405)


# ───────── desempenho ─────────
def test_mapa_com_4000_empresas_e_rapido_e_usa_indice(cli, owner_engine):
    import random
    rnd = random.Random(1)
    with owner_engine.begin() as db:
        sit = db.execute(text("SELECT id FROM situacoes LIMIT 1")).scalar_one()
        db.execute(text("INSERT INTO empresas (nome, rua, numero, numero_base, bairro, cidade, estado, latitude, longitude, geocode_status, situacao_id) "
                        "VALUES (:n, :r, :nu, :nb, 'Centro', :c, 'PE', :la, :lo, 'localizado', :s)"),
                   [{"n": f"E{i}", "r": f"Rua {i % 150}", "nu": str(i % 400), "nb": i % 400, "c": ("Paulista", "Olinda", "Igarassu")[i % 3],
                     "la": -7.9 - rnd.random() * 0.2, "lo": -34.95 + rnd.random() * 0.2, "s": sit} for i in range(4000)])
    t = time.time(); r = mapa(cli); t_todos = time.time() - t
    assert r["total"] == 4000 and len(r["marcadores"]) == 4000
    t = time.time(); r2 = mapa(cli, cidade="Olinda", rua="Rua 1", paridade="pares"); t_filtro = time.time() - t
    t = time.time(); mapa(cli, sul=-8.0, norte=-7.95, oeste=-34.95, leste=-34.9); t_bbox = time.time() - t
    t = time.time(); cli.get("/api/mapa/proximas", params={"lat": -8.0, "lon": -34.85, "raio_m": 2000}); t_prox = time.time() - t
    assert max(t_todos, t_filtro, t_bbox, t_prox) < 2.0, (t_todos, t_filtro, t_bbox, t_prox)
    assert r2["total"] == cli.get("/api/empresas", params={"cidade": "Olinda", "rua": "Rua 1", "paridade": "pares", "page_size": 1}).json()["total"] > 0
    with owner_engine.begin() as db:
        plan = " ".join(str(x) for x in db.execute(text("EXPLAIN QUERY PLAN SELECT id FROM empresas WHERE latitude BETWEEN -8.0 AND -7.95 AND longitude BETWEEN -34.95 AND -34.9")))
    assert "empresas_geo_idx" in plan
