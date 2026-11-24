"""Toda empresa nova (cadastro, importação) ou com endereço alterado deve ser mapeada sozinha."""
import io
import time

import pytest
from openpyxl import Workbook
from sqlalchemy import text

from app.config import get_settings
from app.modules.geocoding import service as svc
from app.modules.geocoding.auto import Automatico, auto
from app.modules.geocoding.client import GeocoderIndisponivel
from tests.test_geocoding import FakeGeo, emp, item, linha
from tests.test_importacao import HEAD, L, MIME, enviar, fmt, C1, C2


@pytest.fixture(autouse=True)
def _parar_worker():
    yield
    auto.parar()


def esperar(cond, segundos=8.0):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.05)
    return False


def status(owner_engine, i):
    with owner_engine.begin() as db:
        return linha(db, i)["geocode_status"]


def nova(c, h, **kw):
    return c.post("/api/empresas", headers=h, json={"nome": "PADARIA", "rua": "Rua Marcondes", "numero": "100", "cidade": "Paulista", "estado": "PE", **kw})


# ───────── empresa nova cadastrada ─────────
def test_empresa_nova_cadastrada_e_mapeada_sozinha(login, owner_engine):
    c, h = login("oper", "operador")
    geo = FakeGeo(lambda q: [item(lat=-7.9407, lon=-34.8728)])
    auto.iniciar(lambda: geo, intervalo=0.05)
    r = nova(c, h)
    assert r.status_code == 201 and r.json()["geocode_status"] in ("pendente", "localizado")
    assert esperar(lambda: status(owner_engine, r.json()["id"]) == "localizado")
    with owner_engine.begin() as db:
        e = linha(db, r.json()["id"])
    assert (e["latitude"], e["longitude"]) == (-7.9407, -34.8728) and e["geocoded_at"]
    m = c.get("/api/mapa/empresas").json()                                  # e já aparece no mapa
    assert m["com_localizacao"] == 1 and m["marcadores"][0]["id"] == r.json()["id"]


def test_varias_empresas_novas_em_sequencia_todas_mapeadas(login, owner_engine):
    c, h = login("oper", "operador")
    auto.iniciar(lambda: FakeGeo(), intervalo=0.05)
    ids = [nova(c, h, rua=f"Rua {n}").json()["id"] for n in range(15)]
    assert esperar(lambda: all(status(owner_engine, i) == "localizado" for i in ids))


def test_empresa_sem_endereco_nao_trava_a_fila_e_vai_para_conferencia(login, owner_engine):
    c, h = login("oper", "operador")
    geo = FakeGeo()
    auto.iniciar(lambda: geo, intervalo=0.05)
    sem = c.post("/api/empresas", headers=h, json={"nome": "SEM ENDERECO"}).json()["id"]
    com = nova(c, h).json()["id"]
    assert esperar(lambda: status(owner_engine, com) == "localizado" and status(owner_engine, sem) == "nao_encontrado")
    with owner_engine.begin() as db:
        assert linha(db, sem)["geocode_erro"] and "Rua Marcondes" not in str(geo.chamadas[:0])


def test_endereco_alterado_e_remapeado_sozinho_preservando_o_antigo_ate_la(login, owner_engine):
    c, h = login("oper", "operador")
    auto.iniciar(lambda: FakeGeo(lambda q: [item(lat=-7.50, lon=-34.80)]), intervalo=0.05)
    e = nova(c, h).json()
    assert esperar(lambda: status(owner_engine, e["id"]) == "localizado")
    auto.parar()                                                              # worker parado: dá para ver o estado intermediário
    v = c.get(f"/api/empresas/{e['id']}").json()["version"]
    r = c.patch(f"/api/empresas/{e['id']}", headers=h, json={"version": v, "rua": "Rua NOVA"}).json()
    assert r["geocode_status"] == "pendente" and r["geocode_desatualizada"] is True and (r["latitude"], r["longitude"]) == (-7.50, -34.80)
    assert c.get("/api/mapa/empresas").json()["com_localizacao"] == 1       # o marcador antigo segue no mapa
    auto.iniciar(lambda: FakeGeo(lambda q: [item(lat=-7.60, lon=-34.85)]), intervalo=0.05)
    assert esperar(lambda: status(owner_engine, e["id"]) == "localizado" and c.get(f"/api/empresas/{e['id']}").json()["latitude"] == -7.60)
    assert c.get(f"/api/empresas/{e['id']}").json()["geocode_desatualizada"] is False


def test_editar_sem_mudar_endereco_nao_gera_nova_consulta(login, owner_engine):
    c, h = login("oper", "operador")
    geo = FakeGeo()
    auto.iniciar(lambda: geo, intervalo=0.05)
    e = nova(c, h).json()
    assert esperar(lambda: status(owner_engine, e["id"]) == "localizado")
    n = len(geo.chamadas)
    v = c.get(f"/api/empresas/{e['id']}").json()["version"]
    c.patch(f"/api/empresas/{e['id']}", headers=h, json={"version": v, "email": "x@y.com", "nome_fantasia": "Novo nome"})
    time.sleep(0.5)
    assert len(geo.chamadas) == n and status(owner_engine, e["id"]) == "localizado"


# ───────── importação ─────────
def test_importacao_confirmada_mapeia_as_empresas_novas(login, owner_engine):
    c, h = login("adm", "admin")
    auto.iniciar(lambda: FakeGeo(), intervalo=0.05)
    wb = Workbook(); ws = wb.active; ws.append(HEAD)
    for n in range(12):
        ws.append(L(nome=f"E{n}", cnpj="", rua=f"Rua {n}"))
    b = io.BytesIO(); wb.save(b)
    imp = enviar(c, h, b.getvalue()).json()
    assert c.post(f"/api/importacoes/{imp['id']}/confirmar", json={"modo": "novos"}, headers=h).status_code == 200
    def todas():
        with owner_engine.begin() as db:
            return db.execute(text("SELECT count(*) FROM empresas WHERE geocode_status='localizado'")).scalar_one() == 12
    assert esperar(todas)


def test_importacao_que_altera_endereco_volta_para_a_fila(login, owner_engine):
    c, h = login("adm", "admin")
    e = c.post("/api/empresas", headers=h, json={"nome": "X", "cnpj": C1, "rua": "Rua A", "numero": "1", "cidade": "Olinda", "estado": "PE"}).json()
    with owner_engine.begin() as db:
        db.execute(text("UPDATE empresas SET latitude=-8.0, longitude=-34.9, geocode_status='localizado' WHERE id=:i"), {"i": e["id"]})
    wb = Workbook(); ws = wb.active; ws.append(HEAD); ws.append(L(rua="Rua NOVA", numero="9"))
    b = io.BytesIO(); wb.save(b)
    imp = enviar(c, h, b.getvalue()).json()
    c.post(f"/api/importacoes/{imp['id']}/confirmar", json={"modo": "atualizar"}, headers=h)
    with owner_engine.begin() as db:
        x = linha(db, e["id"])
    assert (x["geocode_status"], x["geocode_desatualizada"], x["latitude"]) == ("pendente", 1, -8.0)


# ───────── robustez ─────────
def test_servico_fora_do_ar_nao_perde_nada_recua_e_depois_recupera(login, owner_engine):
    c, h = login("oper", "operador")
    caiu = {"sim": True}
    geo = FakeGeo(lambda q: GeocoderIndisponivel("x") if caiu["sim"] else [item()])
    a = Automatico()
    a._criar = lambda: geo
    ids = [nova(c, h, rua=f"Rua {n}").json()["id"] for n in range(5)]
    assert a.rodada() == 0 and a.espera_extra >= 60                           # parou após 3 falhas e vai esperar
    assert len(geo.chamadas) == 3 and all(status(owner_engine, i) == "pendente" for i in ids)
    caiu["sim"] = False
    a.espera_extra = 0
    assert a.rodada() == 5 and all(status(owner_engine, i) == "localizado" for i in ids)


def test_fila_vazia_nao_consulta_nada_e_nao_entra_em_loop(login, owner_engine):
    geo = FakeGeo()
    a = Automatico()
    a._criar = lambda: geo
    assert a.rodada() == 0 and geo.chamadas == []
    with owner_engine.begin() as db:
        emp(db, latitude=-8, longitude=-34.9, geocode_status="localizado")
    assert a.rodada() == 0 and geo.chamadas == []


def test_empresa_nao_encontrada_nao_e_reprocessada_para_sempre(login, owner_engine):
    """Regressão: endereço alterado + busca sem resultado deixava a empresa na fila eternamente."""
    with owner_engine.begin() as db:
        i = emp(db, latitude=-8.0, longitude=-34.9, geocode_status="pendente", geocode_desatualizada=1)
    geo = FakeGeo(lambda q: [])
    a = Automatico()
    a._criar = lambda: geo
    assert a.rodada() == 1
    n = len(geo.chamadas)
    assert a.rodada() == 0 and len(geo.chamadas) == n                         # não volta a tentar sozinha
    with owner_engine.begin() as db:
        x = linha(db, i)
    assert x["geocode_status"] == "revisao" and (x["latitude"], x["longitude"]) == (-8.0, -34.9) and x["geocode_desatualizada"] == 1


def test_lote_manual_e_script_terminam_mesmo_com_empresa_nao_encontrada_com_coordenada(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        emp(db, latitude=-8.0, longitude=-34.9, geocode_status="pendente", geocode_desatualizada=1)
        for n in range(3):
            emp(db, f"E{n}", rua=f"Rua {n}")
    assert main([], geo=FakeGeo(lambda q: [] if q.get("street", "").endswith("Marcondes") else [item()]), saida=lambda *_: None) == 0


def test_automatico_respeita_a_trava_de_outro_geocodificador(login, owner_engine):
    c, h = login("oper", "operador")
    nova(c, h)
    geo = FakeGeo()
    a = Automatico()
    a._criar = lambda: geo
    with owner_engine.connect() as db:
        assert svc.adquirir(db, "cli-1") is True
        assert a.rodada() == 0 and geo.chamadas == []                         # o script de terminal está rodando
        svc.liberar(db, "cli-1")
    assert a.rodada() == 1


def test_erro_inesperado_nao_derruba_a_thread(login, owner_engine, monkeypatch):
    c, h = login("oper", "operador")
    chamadas = {"n": 0}
    orig = svc.processar_lote

    def instavel(*a, **k):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("bug")
        return orig(*a, **k)
    monkeypatch.setattr(svc, "processar_lote", instavel)
    import app.modules.geocoding.auto as mod
    monkeypatch.setattr(mod, "ESPERA_ERRO", 0.05)
    auto.iniciar(lambda: FakeGeo(), intervalo=0.05)
    i = nova(c, h).json()["id"]
    assert esperar(lambda: status(owner_engine, i) == "localizado") and auto.ativo


def test_parar_encerra_a_thread_e_acordar_sem_servico_nao_faz_mal():
    a = Automatico()
    a.acordar()
    assert a.ativo is False
    a.iniciar(lambda: FakeGeo(), intervalo=0.05)
    assert a.ativo is True
    a.parar()
    assert a.ativo is False


def test_desligado_por_configuracao_nao_inicia(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient
    assert get_settings().geocoder_auto is False        # testes rodam sem o serviço
    with TestClient(app):
        assert auto.ativo is False


def test_ligado_por_configuracao_inicia_com_a_aplicacao_e_para_ao_encerrar(monkeypatch, owner_engine):
    from app.main import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(get_settings(), "geocoder_auto", True)
    monkeypatch.setattr(svc, "criar_geocoder", lambda: FakeGeo())
    with owner_engine.begin() as db:
        i = emp(db)                                      # já estava na fila antes de o servidor subir
    with TestClient(app):
        assert auto.ativo is True
        assert esperar(lambda: status(owner_engine, i) == "localizado")
    assert auto.ativo is False


def test_resumo_informa_se_a_automatica_esta_ativa_e_a_estimativa(login, owner_engine):
    c, h = login("viewer", "visualizador")
    with owner_engine.begin() as db:
        for n in range(100):
            emp(db, f"E{n}")
    r = c.get("/api/geocodificacao/resumo").json()
    assert r["automatica"] is False and r["pendente"] == 100 and r["estimativa_min"] == 3     # 100 × 1,4 s ≈ 3 min
    auto.iniciar(lambda: FakeGeo(lambda q: GeocoderIndisponivel("x")), intervalo=30)
    assert c.get("/api/geocodificacao/resumo").json()["automatica"] is True
