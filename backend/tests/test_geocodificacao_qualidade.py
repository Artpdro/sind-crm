"""Motor de busca melhorado (a partir dos padrões da planilha real), diagnóstico, teste de conexão e reprocessamento."""
import pytest
from sqlalchemy import text

from app.modules.geocoding import service as svc
from app.modules.geocoding.auto import auto
from app.modules.geocoding.client import GeocoderConfigInvalida, GeocoderIndisponivel
from tests.test_geocoding import FakeGeo, emp, item, linha

E = lambda **kw: {"rua": "Rua Marcondes", "numero_base": 100, "bairro": "Centro", "cidade": "Paulista", "estado": "PE", "cep": "53401000", **kw}  # noqa: E731


# ───────── limpeza dos endereços como aparecem na planilha ─────────
@pytest.mark.parametrize("bruto,esperado", [
    ("Avenida João Pessoa, 120 AC Itapissuma", ("Avenida João Pessoa", 120)),
    ("Avenida Mário Melo, s/n Centro C D V Cruz", ("Avenida Mário Melo", None)),
    ("AV MARIO MELO, BOX 06", ("AV MARIO MELO", None)),
    ("Rodovia PE-15, s/n", ("Rodovia PE-15", None)),
    ("Rua Tal - Loja 2", ("Rua Tal", None)),
    ("Rua Siqueira Campos", ("Rua Siqueira Campos", None)),
    ("  Rua   com   espaços  ", ("Rua com espaços", None)),
])
def test_limpar_rua(bruto, esperado):
    assert svc.limpar_rua(bruto) == esperado


@pytest.mark.parametrize("rua,variantes", [
    ("Rodovia BR-101 Norte", ["Rodovia BR-101"]), ("Rodovia PE-15", ["Rodovia PE-15"]), ("Rod. PE 01", ["Rodovia PE-01"]),
    ("1ª Travessa Madalena", ["1ª Travessa Madalena", "Primeira Travessa Madalena"]), ("3ª Travessa João Alves", ["3ª Travessa João Alves", "Terceira Travessa João Alves"]),
    ("PC Antonio Vitalino", ["Praça Antonio Vitalino"]), ("AV MARIO MELO", ["Avenida MARIO MELO"]), ("R. Dr. Pedro", ["Rua Dr. Pedro"]),
    ("Avenida Duque de Caxias", ["Avenida Duque de Caxias"]),
])
def test_variantes_de_rua(rua, variantes):
    assert svc.variantes_rua(rua) == variantes


@pytest.mark.parametrize("rua,cidade,lugar", [
    ("Itapissuma", "Itapissuma", True), ("Ilha de Itamaracá", "Ilha de Itamaracá", True), ("Área Rural", "Igarassu", True), ("Abreu e Lima", "Abreu e Lima", True),
    ("Olinda", "Paulista", True), ("Zona Rural", "Olinda", True), ("Rua do Sol", "Olinda", False), ("Avenida Brasil", "Paulista", False),
])
def test_rua_que_na_verdade_e_nome_de_lugar(rua, cidade, lugar):
    assert svc.rua_e_lugar(rua, cidade) is lugar


def test_nome_de_municipio_no_campo_rua_nao_vira_busca_de_rua():
    cs = svc.montar_consultas(E(rua="Itapissuma", cidade="Itapissuma", bairro="Centro"))
    assert [c.precisao_esperada for c in cs] == ["cep", "bairro"]
    assert not any("street" in c.campos for c in cs)


def test_consulta_usa_numero_embutido_na_rua_quando_o_campo_numero_esta_vazio():
    cs = svc.montar_consultas(E(rua="Avenida João Pessoa, 120 AC Itapissuma", numero_base=None))
    assert cs[0].campos["street"] == "120 Avenida João Pessoa"


def test_ilha_de_itamaraca_tambem_tenta_itamaraca():
    cidades = [c.campos.get("city") for c in svc.montar_consultas(E(cidade="Ilha de Itamaracá")) if "city" in c.campos]
    assert "Ilha de Itamaracá" in cidades and "Itamaracá" in cidades


def test_estrutura_das_consultas_e_compativel_com_o_nominatim():
    for e in (E(), E(rua="1ª Travessa X", cidade="Ilha de Itamaracá"), E(rua="Rodovia BR-101 Norte")):
        cs = svc.montar_consultas(e)
        assert len({c.chave for c in cs}) == len(cs)                          # sem consultas repetidas
        for c in cs:
            assert c.campos["limit"] == "5"                                   # vários candidatos: dá para escolher o da cidade certa
            if "q" in c.campos:                                               # o Nominatim recusa misturar busca livre com campos estruturados
                assert not ({"street", "city", "state", "postalcode"} & set(c.campos))


def test_busca_livre_leva_o_bairro():
    q = [c.campos["q"] for c in svc.montar_consultas(E()) if "q" in c.campos and c.precisao_esperada == "rua"][0]
    assert q == "Rua Marcondes, Centro, Paulista, Pernambuco, Brasil"


# ───────── escolher e classificar ─────────
def test_escolhe_o_candidato_da_cidade_certa_mesmo_que_nao_seja_o_primeiro():
    c = svc.Consulta({"street": "x"}, "rua")
    itens = [item(house=None, rank=26, addresstype="road", cidade="Recife", lat=-8.05, lon=-34.88),
             item(house=None, rank=26, addresstype="road", cidade="Paulista", lat=-7.94, lon=-34.87)]
    r = svc.escolher(itens, c, E())
    assert r["confere"] is True and r["lat"] == -7.94
    assert svc.escolher([item(addresstype="city", rank=16, house=None)], c, E()) is None


def test_escolhe_o_mais_preciso_entre_os_da_mesma_cidade():
    c = svc.Consulta({"street": "x"}, "endereco")
    itens = [item(house=None, rank=26, addresstype="road", lat=-7.90), item(house="100", lat=-7.95)]
    assert svc.escolher(itens, c, E())["precisao"] == "endereco"


@pytest.mark.parametrize("achado,e,status,obs", [
    ({"precisao": "endereco", "confere": True}, E(), "localizado", None),
    ({"precisao": "rua", "confere": True}, E(), "localizado", "Aproximada pela rua"),
    ({"precisao": "rua", "confere": True}, E(rua="Rodovia PE-15"), "revisao", "Rodovia"),
    ({"precisao": "rua", "confere": True}, E(rua="Estrada do Caenga"), "revisao", "Rodovia/estrada"),
    ({"precisao": "cep", "confere": True}, E(), "revisao", "CEP"),
    ({"precisao": "bairro", "confere": True}, E(), "revisao", "bairro"),
    ({"precisao": "endereco", "confere": False}, E(), "revisao", "cidade/estado"),
])
def test_classificacao_localizado_x_revisao(achado, e, status, obs):
    st, ob = svc.classificar(achado, e)
    assert st == status and (obs is None and ob is None or obs in ob)


def test_bairro_so_vale_como_ultimo_recurso():
    suburb = item(house=None, rank=20, addresstype="suburb")
    assert svc.interpretar(suburb, svc.Consulta({"q": "x"}, "bairro"), E())["precisao"] == "bairro"
    assert svc.interpretar(suburb, svc.Consulta({"q": "x"}, "rua"), E()) is None
    assert svc.interpretar(suburb, svc.Consulta({"street": "x"}, "endereco"), E()) is None


# ───────── processamento com o novo motor ─────────
def test_localizado_pela_rua_grava_observacao_e_aparece_como_aproximado_no_mapa(login, owner_engine):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        i = emp(db, numero="S/N", numero_base=None)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda q: [item(house=None, rank=26, addresstype="road")]), i) == "localizado"
    with owner_engine.begin() as db:
        e = linha(db, i)
    assert e["geocode_precisao"] == "rua" and "Aproximada pela rua" in e["geocode_erro"] and e["geocode_status"] == "localizado"
    m = c.get("/api/mapa/empresas").json()["marcadores"][0]
    assert m["aprox"] is True and m["revisao"] is False


def test_endereco_exato_nao_e_aproximado(login, owner_engine):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        emp(db)
    with owner_engine.connect() as db:
        svc.processar_lote(db, FakeGeo(), 5)
    assert c.get("/api/mapa/empresas").json()["marcadores"][0]["aprox"] is False


def test_rua_que_e_nome_de_municipio_cai_para_cep_e_depois_bairro(owner_engine):
    with owner_engine.begin() as db:
        a = emp(db, "A", rua="Itapissuma", numero=None, numero_base=None, cidade="Itapissuma", bairro="Centro")
        b = emp(db, "B", rua="Itapissuma", numero=None, numero_base=None, cidade="Itapissuma", bairro="Centro", cep=None)
    def so_bairro(q):
        return [item(house=None, rank=20, addresstype="suburb", cidade="Itapissuma")] if "q" in q else []
    geo = FakeGeo(so_bairro)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, a) == "revisao"
        assert svc.processar_empresa(db, geo, b) == "revisao"
    with owner_engine.begin() as db:
        x = linha(db, a)
    assert x["geocode_precisao"] == "bairro" and "bairro" in x["geocode_erro"] and x["latitude"] is not None
    assert not any("street" in q for q in geo.chamadas)                       # nunca procurou "Itapissuma" como se fosse rua


def test_ilha_de_itamaraca_acha_pelo_nome_alternativo(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db, cidade="Ilha de Itamaracá")
    geo = FakeGeo(lambda q: [item(cidade="Itamaracá")] if q.get("city") == "Itamaracá" else [])
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, i) == "localizado"


def test_nenhuma_busca_acha_vira_nao_encontrada_com_motivo(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda q: []), i) == "nao_encontrado"
    with owner_engine.begin() as db:
        assert "tentativa" in linha(db, i)["geocode_erro"]


def test_estado_do_servico_registra_o_ultimo_erro_e_o_ultimo_sucesso(owner_engine):
    svc.ESTADO_SERVICO.update(ultimo_erro=None, ultimo_erro_em=None, ultimo_ok_em=None, consultas=0)
    with owner_engine.begin() as db:
        a, b = emp(db, "A"), emp(db, "B", rua="Rua Outra")
    with owner_engine.connect() as db:
        svc.processar_empresa(db, FakeGeo(lambda q: GeocoderIndisponivel("O serviço respondeu com erro 403.")), a)
        assert svc.ESTADO_SERVICO["ultimo_erro"] == "O serviço respondeu com erro 403." and svc.ESTADO_SERVICO["ultimo_ok_em"] is None
        svc.processar_empresa(db, FakeGeo(), b)
        assert svc.ESTADO_SERVICO["ultimo_ok_em"] and svc.resumo(db)["servico"]["ultimo_erro"].endswith("403.")


# ───────── API: diagnóstico, teste, reprocessar ─────────
def test_diagnostico_mostra_causas_e_exemplos(login, owner_engine):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        emp(db, "ok", latitude=-8, longitude=-34.9, geocode_status="localizado", geocode_precisao="endereco")
        emp(db, "aprox", latitude=-8, longitude=-34.9, geocode_status="localizado", geocode_precisao="rua", geocode_erro="Aproximada pela rua")
        emp(db, "nf1", rua="Rua Inexistente", geocode_status="nao_encontrado", geocode_erro="O serviço não encontrou este endereço (3 tentativa(s)).")
        emp(db, "nf2", rua="Rua Inexistente 2", geocode_status="nao_encontrado", geocode_erro="O serviço não encontrou este endereço (3 tentativa(s)).")
        emp(db, "semrua", rua=None, cidade="Olinda", geocode_status="nao_encontrado", geocode_erro="Endereço sem rua, bairro ou CEP utilizáveis.")
    d = c.get("/api/geocodificacao/diagnostico").json()
    assert d["por_precisao"] == {"endereco": 1, "rua": 1}
    assert d["motivos"][0] == {"status": "nao_encontrado", "motivo": "O serviço não encontrou este endereço (3 tentativa(s)).", "quantidade": 2}
    assert d["sem_rua_ou_cidade"] == 1 and len(d["exemplos_nao_encontradas"]) == 3 and d["resumo"]["nao_encontrado"] == 3


def test_diagnostico_exige_papel_e_login(client, login):
    assert client.get("/api/geocodificacao/diagnostico").status_code == 401
    c, h = login("viewer", "visualizador")
    assert c.get("/api/geocodificacao/diagnostico").status_code == 403


def test_testar_conexao_ok_e_falha_com_mensagem_segura(login, monkeypatch):
    c, h = login("adm", "admin")
    monkeypatch.setattr(svc, "criar_geocoder", lambda: FakeGeo())
    r = c.post("/api/geocodificacao/testar", headers=h).json()
    assert r["ok"] is True and r["resultados"] == 1
    monkeypatch.setattr(svc, "criar_geocoder", lambda: FakeGeo(lambda q: GeocoderIndisponivel("O serviço respondeu com erro 403.")))
    r = c.post("/api/geocodificacao/testar", headers=h).json()
    assert r["ok"] is False and "403" in r["mensagem"]
    monkeypatch.setattr(svc, "criar_geocoder", lambda: FakeGeo(lambda q: []))
    assert c.post("/api/geocodificacao/testar", headers=h).json()["ok"] is False
    monkeypatch.setattr(svc, "criar_geocoder", lambda: (_ for _ in ()).throw(GeocoderConfigInvalida("http://10.0.0.1")))
    r = c.post("/api/geocodificacao/testar", headers=h)
    assert r.status_code == 503 and "10.0.0.1" not in r.text


def test_testar_conexao_so_admin_e_com_limite(login, monkeypatch):
    monkeypatch.setattr(svc, "criar_geocoder", lambda: FakeGeo())
    c, h = login("oper", "operador")
    assert c.post("/api/geocodificacao/testar", headers=h).status_code == 403
    a, ha = login("adm", "admin")
    codes = [a.post("/api/geocodificacao/testar", headers=ha).status_code for _ in range(22)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]


def test_reprocessar_escopos_preserva_coordenadas_e_correcoes_manuais(login, owner_engine, monkeypatch):
    c, h = login("adm", "admin")
    acordou = []
    monkeypatch.setattr(auto, "acordar", lambda: acordou.append(1))
    with owner_engine.begin() as db:
        nf = emp(db, "nf", geocode_status="nao_encontrado", geocode_erro="x")
        rev = emp(db, "rev", latitude=-8, longitude=-34.9, geocode_status="revisao", geocode_precisao="cep")
        man = emp(db, "man", latitude=-8.1, longitude=-34.9, geocode_status="revisao", geocode_precisao="manual")
        ok = emp(db, "ok", latitude=-8.2, longitude=-34.9, geocode_status="localizado", geocode_precisao="endereco")
        db.execute(text("INSERT INTO geocode_cache (chave, encontrado) VALUES ('antiga', 0)"))
    r = c.post("/api/geocodificacao/reprocessar", json={"escopo": "nao_encontradas"}, headers=h).json()
    assert r["empresas_na_fila"] == 1 and acordou
    with owner_engine.begin() as db:
        assert linha(db, nf)["geocode_status"] == "pendente" and linha(db, rev)["geocode_status"] == "revisao"
        assert db.execute(text("SELECT count(*) FROM geocode_cache")).scalar_one() == 0           # respostas negativas antigas esquecidas
    assert c.post("/api/geocodificacao/reprocessar", json={"escopo": "revisao"}, headers=h).json()["empresas_na_fila"] == 1
    with owner_engine.begin() as db:
        x = linha(db, rev)
        assert (x["geocode_status"], x["latitude"], x["geocode_desatualizada"]) == ("pendente", -8, 1)   # ponto antigo preservado
        assert linha(db, man)["geocode_status"] == "revisao"                                           # manual: nunca refeito
    r = c.post("/api/geocodificacao/reprocessar", json={"escopo": "todas"}, headers=h).json()
    assert r["empresas_na_fila"] == 1                                                                  # só a "ok": nf e rev já estavam na fila; nunca a manual
    with owner_engine.begin() as db:
        assert linha(db, man)["geocode_status"] == "revisao" and linha(db, ok)["geocode_status"] == "pendente"


def test_reprocessar_validacoes_e_permissoes(login):
    c, h = login("oper", "operador")
    assert c.post("/api/geocodificacao/reprocessar", json={"escopo": "revisao"}, headers=h).status_code == 403
    a, ha = login("adm", "admin")
    for corpo in [{"escopo": "tudo"}, {}, {"escopo": "revisao", "extra": 1}, {"escopo": "1; DROP TABLE empresas"}]:
        assert a.post("/api/geocodificacao/reprocessar", json=corpo, headers=ha).status_code == 422


def test_reprocessar_e_reprocessado_pelo_worker(login, owner_engine):
    c, h = login("adm", "admin")
    with owner_engine.begin() as db:
        i = emp(db, geocode_status="nao_encontrado", geocode_erro="x")
    c.post("/api/geocodificacao/reprocessar", json={"escopo": "nao_encontradas"}, headers=h)
    with owner_engine.connect() as db:
        assert svc.processar_lote(db, FakeGeo(), 5)["localizado"] == 1
    with owner_engine.begin() as db:
        assert linha(db, i)["geocode_status"] == "localizado"


# ───────── migração v4 ─────────
def test_migracao_v4_preserva_precisao_e_aceita_bairro(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    from app.schema import MIGRACOES, init_db
    caminho = tmp_path / "v3.db"
    cx = sqlite3.connect(caminho)
    for v, sql in MIGRACOES[:3]:
        cx.executescript(sql)
    cx.execute("INSERT INTO empresas (nome, latitude, longitude, geocode_status, geocode_precisao) VALUES ('A', -8, -34.9, 'localizado', 'rua')")
    cx.execute("INSERT INTO empresas (nome, geocode_precisao) VALUES ('B', 'manual')")
    cx.commit(); cx.close()
    e = create_engine(f"sqlite:///{caminho}")
    assert init_db(e) is True
    with e.begin() as c:
        assert c.execute(text("SELECT nome, geocode_precisao, latitude FROM empresas ORDER BY id")).all() == [("A", "rua", -8.0), ("B", "manual", None)]
        c.execute(text("INSERT INTO empresas (nome, geocode_precisao) VALUES ('C', 'bairro')"))
        assert c.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars().all() == [1, 2, 3, 4, 5]
    with pytest.raises(IntegrityError):
        with e.begin() as c:
            c.execute(text("INSERT INTO empresas (nome, geocode_precisao) VALUES ('D', 'inventado')"))


# ───────── script de terminal ─────────
def test_cli_diagnostico_funciona_sem_internet(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        emp(db, "nf", rua="Rua Inexistente", geocode_status="nao_encontrado", geocode_erro="O serviço não encontrou este endereço (3 tentativa(s)).")
        emp(db, "p")
    out = []
    assert main(["--diagnostico"], geo=None, saida=out.append) == 0
    texto = "\n".join(out)
    assert "não encontradas 1" in texto and "pendentes 1" in texto and "Rua Inexistente" in texto and "Motivos mais comuns" in texto


def test_cli_testar_conexao(owner_engine):
    from scripts.geocode import main
    out = []
    assert main(["--testar"], geo=FakeGeo(), saida=out.append) == 0 and out[-1].startswith("OK")
    out = []
    assert main(["--testar"], geo=FakeGeo(lambda q: GeocoderIndisponivel("O serviço respondeu com erro 403.")), saida=out.append) == 1
    assert out[-1].startswith("FALHOU") and "403" in out[-1]


def test_cli_dry_run_detalhado_mostra_as_buscas_e_o_resumo(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        emp(db, "a")
        emp(db, "b", rua="Rua Sem Resultado", cep=None)
    geo = FakeGeo(lambda q: [] if "Sem Resultado" in str(q) else [item()])
    out = []
    assert main(["--dry-run", "--detalhe", "--amostra", "5"], geo=geo, saida=out.append) == 0
    texto = "\n".join(out)
    assert "LOCALIZADA" in texto and "NÃO ENCONTRADA" in texto and "✓" in texto and "✗" in texto and "Resumo: localizadas 1" in texto
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas WHERE latitude IS NOT NULL")).scalar_one() == 0


def test_cli_refazer_devolve_a_fila_e_processa(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        i = emp(db, geocode_status="nao_encontrado", geocode_erro="x")
    out = []
    assert main(["--refazer", "nao_encontradas"], geo=FakeGeo(), saida=out.append) == 0
    assert any("1 empresas devolvidas" in x for x in out)
    with owner_engine.begin() as db:
        assert linha(db, i)["geocode_status"] == "localizado"


# ───────── aprendizado: pular a busca "com número" quando ela quase nunca acha ─────────
def _numeradas(geo):
    return [c for c in geo.chamadas if c.get("street", "")[:1].isdigit()]


def _criar(owner_engine, n, **kw):
    with owner_engine.begin() as db:
        return [emp(db, f"E{i}", rua=f"Rua {i}", numero=str(10 + i), numero_base=10 + i, **kw) for i in range(n)]


def test_pula_a_busca_com_numero_quando_ela_quase_nunca_acha(owner_engine):
    ids = _criar(owner_engine, 80)
    geo = FakeGeo(lambda q: [] if q.get("street", "")[:1].isdigit() else [item(house=None, rank=26, addresstype="road")])
    with owner_engine.connect() as db:
        for i in ids:
            assert svc.processar_empresa(db, geo, i) == "localizado"      # todas acabam localizadas pela rua
    n = len(_numeradas(geo))
    assert 40 <= n < 50                                                   # 40 de amostra + algumas sondas; as outras ~35 foram puladas
    assert svc.NUMERO["puladas"] > 30


def test_nao_pula_quando_a_busca_com_numero_funciona(owner_engine):
    ids = _criar(owner_engine, 60)
    geo = FakeGeo()                                                       # número sempre achado
    with owner_engine.connect() as db:
        for i in ids:
            svc.processar_empresa(db, geo, i)
    assert len(_numeradas(geo)) == 60 and svc.NUMERO["puladas"] == 0


def test_sonda_periodica_percebe_que_o_mapa_melhorou(owner_engine):
    ids = _criar(owner_engine, 130)
    melhorou = {"sim": False}
    def fn(q):
        if q.get("street", "")[:1].isdigit():
            return [item(house="1")] if melhorou["sim"] else []
        return [item(house=None, rank=26, addresstype="road")]
    geo = FakeGeo(fn)
    with owner_engine.connect() as db:
        for i in ids[:60]:
            svc.processar_empresa(db, geo, i)
        melhorou["sim"] = True
        for i in ids[60:]:
            svc.processar_empresa(db, geo, i)
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas WHERE geocode_precisao = 'endereco'")).scalar_one() >= 2   # as sondas acharam endereço exato
    assert svc.NUMERO["acertos"] >= 2


def test_pular_pode_ser_desligado_e_o_dry_run_nunca_pula(owner_engine, monkeypatch):
    from app.config import get_settings
    from scripts.geocode import main
    ids = _criar(owner_engine, 70)
    geo = FakeGeo(lambda q: [] if q.get("street", "")[:1].isdigit() else [item(house=None, rank=26, addresstype="road")])
    monkeypatch.setattr(get_settings(), "geocoder_pular_numero_raro", False)
    with owner_engine.connect() as db:
        for i in ids:
            svc.processar_empresa(db, geo, i)
    assert len(_numeradas(geo)) == 70 and svc.NUMERO["puladas"] == 0
    svc.resetar_estatisticas()
    with owner_engine.begin() as db:  # novas pendentes para a simulação
        for n in range(5):
            emp(db, f"N{n}", rua=f"Rua Nova {n}", numero="7", numero_base=7)
    monkeypatch.setattr(get_settings(), "geocoder_pular_numero_raro", True)
    svc.NUMERO.update(tentativas=100, acertos=0)                          # já "aprendeu" que número não funciona
    geo2 = FakeGeo(lambda q: [] if q.get("street", "")[:1].isdigit() else [item(house=None, rank=26, addresstype="road")])
    assert main(["--dry-run", "--amostra", "5"], geo=geo2, saida=lambda *_: None) == 0
    assert len(_numeradas(geo2)) == 5                                     # a simulação mostra TODAS as buscas


def test_busca_com_numero_ja_em_cache_nao_e_pulada(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db, "A", numero="100", numero_base=100)
    svc.NUMERO.update(tentativas=100, acertos=0)
    geo = FakeGeo()
    with owner_engine.connect() as db:                                    # cache já tem a resposta do número exato
        e = svc.carregar(db, i)
        c = svc.montar_consultas(e)[0]
        svc._cache_put(db, c.chave, {"lat": -7.9, "lon": -34.9, "precisao": "endereco", "confere": True}); db.commit()
        assert svc.processar_empresa(db, geo, i) == "localizado" and geo.chamadas == []
    with owner_engine.begin() as db:
        assert linha(db, i)["geocode_precisao"] == "endereco"


# ───────── motivos claros para "não encontrada" ─────────
@pytest.mark.parametrize("e,trecho", [
    (E(rua="Itapissuma", cidade="Itapissuma", bairro=None), "só o município"),
    (E(rua="Área Rural", cidade="Igarassu", bairro=None), "só o município"),
    (E(rua=None, cidade=None, bairro=None, cep=None), "sem rua, bairro ou CEP"),
    (E(), "tentativa(s)"),
])
def test_motivos_de_nao_encontrada(e, trecho):
    tent = [{"nivel": "rua", "achou": False}] if "tentativa" in trecho else []
    assert trecho in svc.motivo_nao_encontrada(e, tent)


def test_empresa_so_com_municipio_explica_no_cadastro(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db, rua="Itapissuma", numero="113", numero_base=113, cidade="Itapissuma", cep=None)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda q: []), i) == "nao_encontrado"
    with owner_engine.begin() as db:
        assert "só o município" in linha(db, i)["geocode_erro"]


# ───────── configuração: comentário na mesma linha NUNCA pode virar valor ─────────
def _sem_ambiente(monkeypatch):
    for k in ("APP_ENV", "GEOCODER_AUTO", "COOKIE_SECURE", "ALLOWED_ORIGINS", "FRONTEND_BASE_URL", "GEOCODER_EMAIL", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)   # variáveis de ambiente têm prioridade sobre o arquivo: aqui só o arquivo conta


def test_env_example_e_lido_corretamente_sem_comentarios_como_valor(monkeypatch):
    from app.config import Settings
    _sem_ambiente(monkeypatch)
    s = Settings(_env_file=".env.example")
    assert s.geocoder_email == "" and s.geocoder_auto is True and s.geocoder_min_interval == 1.1 and s.geocoder_allow_local is False
    assert s.app_env == "development" and s.cookie_secure is False and s.smtp_host == "" and s.database_url.startswith("sqlite:///")
    for v in s.model_dump().values():
        assert not (isinstance(v, str) and "#" in v), v


def test_arquivo_env_com_valor_vazio_e_comentario_na_linha(tmp_path, monkeypatch):
    from app.config import Settings
    _sem_ambiente(monkeypatch)
    f = tmp_path / ".env"
    f.write_text("GEOCODER_EMAIL=                 # SEU contato (ex.: ti@x.org)\nSMTP_HOST=   # sem smtp\nGEOCODER_MIN_INTERVAL=1.5   # seg\n")
    s = Settings(_env_file=str(f))
    assert s.geocoder_email == "" and s.smtp_host == "" and s.geocoder_min_interval == 1.5


@pytest.mark.parametrize("v", ["isso nao e email", "a@b", "x@@y.com", "# comentario@x.org", "a b@c.com", "a@b.com; rm -rf"])
def test_email_invalido_do_geocodificador_e_recusado_com_mensagem_clara(v, tmp_path, monkeypatch):
    from pydantic import ValidationError
    _sem_ambiente(monkeypatch)
    from app.config import Settings
    f = tmp_path / ".env"
    f.write_text(f"GEOCODER_EMAIL={v}\n")
    if v.startswith("#"):
        assert Settings(_env_file=str(f)).geocoder_email == ""          # começa com '#': é comentário
    else:
        with pytest.raises(ValidationError) as ex:
            Settings(_env_file=str(f))
        assert "GEOCODER_EMAIL" in str(ex.value)


def test_email_valido_e_aceito_e_vai_no_user_agent(tmp_path, monkeypatch):
    from app.config import Settings
    _sem_ambiente(monkeypatch)
    from app.modules.geocoding.client import Nominatim
    f = tmp_path / ".env"
    f.write_text("GEOCODER_EMAIL=ti@sindnorte.org.br   # contato\n")
    s = Settings(_env_file=str(f))
    assert s.geocoder_email == "ti@sindnorte.org.br"
    g = Nominatim("https://nominatim.example.org", s.geocoder_email)
    assert "ti@sindnorte.org.br" in g.ua and "email=ti%40sindnorte.org.br" in g.montar_url({"q": "x"})


@pytest.mark.parametrize("lixo", ["# SEU contato (ex.: ti@x.org)", "texto qualquer", "a@b", "x\r\nHost: evil.com", ""])
def test_cliente_nunca_envia_contato_invalido(lixo):
    from app.modules.geocoding.client import Nominatim
    g = Nominatim("https://nominatim.example.org", lixo)
    assert g.email == "" and "contato" not in g.ua and "email=" not in g.montar_url({"q": "x"}) and "#" not in g.ua and "\n" not in g.ua
