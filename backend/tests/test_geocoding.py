import json
import socket
import urllib.error
import urllib.parse

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.modules.geocoding import service as svc
from app.modules.geocoding.client import (GeocoderConfigInvalida, GeocoderIndisponivel, Nominatim, Resposta, _SemRedirect,
                                          validar_destino)
from tests.conftest import ORIGIN, PASSWORD


# ───────── apoio ─────────
class FakeGeo:
    def __init__(self, fn=None):
        self.fn, self.chamadas = fn or (lambda c: [item()]), []

    def buscar(self, consulta):
        self.chamadas.append(dict(consulta))
        r = self.fn(consulta)
        if isinstance(r, Exception):
            raise r
        return Resposta(r or [])


def item(lat=-7.94, lon=-34.87, rank=30, house="100", cidade="Paulista", uf="BR-PE", addresstype="building"):
    addr = {"city": cidade, "ISO3166-2-lvl4": uf}
    if house:
        addr["house_number"] = house
    return {"lat": str(lat), "lon": str(lon), "place_rank": rank, "addresstype": addresstype, "address": addr}


def emp(db, nome="E", **kw):
    cols = {"nome": nome, "rua": "Rua Marcondes", "numero": "100", "numero_base": 100, "cidade": "Paulista", "estado": "PE",
            "cep": "53401000", **kw}
    return db.execute(text(f"INSERT INTO empresas ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) RETURNING id"), cols).scalar_one()  # noqa: S608


def linha(db, i):
    return dict(db.execute(text("SELECT * FROM empresas WHERE id=:i"), {"i": i}).mappings().one())


class Corpo:
    def __init__(self, b):
        self.b = b

    def read(self, n=-1):
        return self.b if n < 0 else self.b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def dns_publico(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def nom(body=b"[]", **kw):
    return Nominatim("https://nominatim.example.org", abrir=lambda req, timeout: Corpo(body), **kw)


# ───────── Cliente: SSRF, política de uso, falhas ─────────
def test_endereco_malicioso_nunca_vira_host_nem_caminho():
    g = nom(email="contato@sindnorte.org")
    for lixo in ["@evil.com/../x?y=1#", "http://169.254.169.254/latest", "a\r\nHost: evil.com", "x&format=xml&email=a@b", "../../etc/passwd"]:
        u = urllib.parse.urlsplit(g.montar_url({"street": lixo, "city": lixo}))
        assert u.scheme == "https" and u.netloc == "nominatim.example.org" and u.path == "/search"
        assert "\r" not in u.geturl() and "\n" not in u.geturl() and " " not in u.geturl()
        q = urllib.parse.parse_qs(u.query)
        assert q["format"] == ["jsonv2"] and q["street"] == [lixo] and q["email"] == ["contato@sindnorte.org"]  # nada de parâmetro injetado


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "https://127.0.0.1/", "https://localhost/", "https://[::1]/", "https://169.254.169.254/",
                                 "https://10.0.0.5/", "https://192.168.1.10/", "https://172.16.0.1/", "https://0.0.0.0/",
                                 "file:///etc/passwd", "ftp://nominatim.example.org/", "gopher://x/", "https://user:pass@nominatim.example.org/",
                                 "http://nominatim.example.org/", "https:///sem-host", "javascript:alert(1)"])
def test_destinos_proibidos(url, dns_publico):
    with pytest.raises((GeocoderConfigInvalida, GeocoderIndisponivel)):
        validar_destino(url)


def test_hostname_que_resolve_para_rede_interna_e_bloqueado(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.5", 443))])
    with pytest.raises(GeocoderConfigInvalida):
        validar_destino("https://parece-inocente.example.org/")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(GeocoderConfigInvalida):  # basta UM endereço interno entre os resolvidos
        validar_destino("https://misto.example.org/")


def test_nominatim_proprio_em_localhost_so_com_permissao():
    with pytest.raises(GeocoderConfigInvalida):
        validar_destino("http://127.0.0.1:8080/")
    assert validar_destino("http://127.0.0.1:8080/", permitir_local=True).hostname == "127.0.0.1"
    with pytest.raises(GeocoderConfigInvalida):
        validar_destino("http://93.184.216.34/", permitir_local=True)  # http só para loopback


def test_destino_validado_a_cada_consulta(monkeypatch):
    g = Nominatim("https://127.0.0.1", abrir=lambda r, timeout: pytest.fail("não deveria chegar na rede"))
    with pytest.raises(GeocoderConfigInvalida):
        g.buscar({"street": "x"})


def test_redirecionamentos_nao_sao_seguidos():
    assert _SemRedirect().redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/") is None


def test_uma_consulta_por_segundo(dns_publico):
    t, dormiu = [100.0], []
    g = nom(relogio=lambda: t[0], sleeper=lambda s: (dormiu.append(s), t.__setitem__(0, t[0] + s)))
    g.buscar({"street": "a"})
    assert dormiu == []
    t[0] += 0.2
    g.buscar({"street": "b"})
    assert len(dormiu) == 1 and 0.85 < dormiu[0] < 0.95
    t[0] += 5
    g.buscar({"street": "c"})
    assert len(dormiu) == 1  # já passou mais de 1,1 s: não espera


def test_user_agent_identifica_o_sistema_e_o_contato(dns_publico):
    vistos = []
    g = Nominatim("https://nominatim.example.org", email="contato@sindnorte.org", abrir=lambda req, timeout: (vistos.append(req), Corpo(b"[]"))[1])
    g.buscar({"street": "x"})
    assert "SindnorteEmpresas" in vistos[0].get_header("User-agent") and "contato@sindnorte.org" in vistos[0].get_header("User-agent")


@pytest.mark.parametrize("erro", [urllib.error.HTTPError("u", 429, "x", {}, None), urllib.error.HTTPError("u", 503, "x", {}, None),
                                  urllib.error.URLError("sem rede"), TimeoutError(), ConnectionResetError()])
def test_falhas_de_rede_viram_indisponivel_e_nao_nao_encontrado(erro, dns_publico):
    def abrir(req, timeout):
        raise erro
    with pytest.raises(GeocoderIndisponivel):
        Nominatim("https://nominatim.example.org", abrir=abrir).buscar({"street": "x"})


@pytest.mark.parametrize("corpo", [b"<html>nao e json</html>", b'{"erro": 1}', b'"texto"', b"x" * 300_000])
def test_respostas_invalidas_ou_gigantes(corpo, dns_publico):
    with pytest.raises(GeocoderIndisponivel):
        nom(corpo).buscar({"street": "x"})


def test_resposta_valida_filtra_itens_estranhos(dns_publico):
    r = nom(json.dumps([item(), "lixo", 5, None]).encode()).buscar({"street": "x"})
    assert len(r.itens) == 1


# ───────── Consultas e interpretação ─────────
def test_consultas_do_mais_preciso_para_o_menos():
    cs = svc.montar_consultas({"rua": "R. Marcondes", "numero_base": 100, "cidade": "Paulista", "estado": "PE", "cep": "53401000"})
    assert [c.precisao_esperada for c in cs] == ["endereco", "rua", "rua", "cep"]
    assert cs[0].campos["street"] == "100 Rua Marcondes" and cs[0].campos["state"] == "Pernambuco" and cs[0].campos["country"] == "Brasil"
    assert cs[1].campos["street"] == "Rua Marcondes" and "Rua Marcondes" in cs[2].campos["q"] and cs[3].campos["postalcode"] == "53401-000"


def test_consultas_sem_numero_sem_rua_e_sem_nada():
    assert [c.precisao_esperada for c in svc.montar_consultas({"rua": "Av. Brasil", "numero_base": None, "cidade": "Olinda", "estado": "PE"})] == ["rua", "rua"]
    assert [c.precisao_esperada for c in svc.montar_consultas({"rua": None, "cidade": "Olinda", "cep": "53620000"})] == ["cep"]
    assert svc.montar_consultas({"rua": "X", "cidade": None, "cep": "123"}) == []
    assert svc.expandir_rua("AV. Brasil") == "Avenida Brasil" and svc.expandir_rua("Rua Nova") == "Rua Nova" and svc.expandir_rua("Rodovia PE-15") == "Rodovia PE-15"


E = {"cidade": "Paulista", "estado": "PE"}
C = svc.Consulta({"street": "x"}, "endereco")


def test_interpretar_precisao():
    assert svc.interpretar(item(), C, E)["precisao"] == "endereco"
    assert svc.interpretar(item(house=None, rank=26, addresstype="road"), C, E)["precisao"] == "rua"
    assert svc.interpretar(item(house=None, rank=21, addresstype="postcode"), svc.Consulta({}, "cep"), E)["precisao"] == "cep"
    assert svc.interpretar(item(house=None, rank=16, addresstype="city"), C, E) is None       # centro de cidade engana: descartado
    assert svc.interpretar(item(house=None, rank=20, addresstype="suburb"), C, E) is None


@pytest.mark.parametrize("lat,lon", [(48.85, 2.35), (0, 0), (40.7, -74.0), (-7.9, 10.0), (91, -34), ("abc", "def"), (float("nan"), -34)])
def test_interpretar_descarta_fora_do_brasil_e_lixo(lat, lon):
    assert svc.interpretar(item(lat=lat, lon=lon), C, E) is None


def test_interpretar_cidade_ou_estado_divergente_pede_revisao():
    assert svc.interpretar(item(cidade="Recife"), C, E)["confere"] is False
    assert svc.interpretar(item(uf="BR-SP"), C, E)["confere"] is False
    assert svc.interpretar(item(cidade="Paulista", uf="BR-PE"), C, E)["confere"] is True
    assert svc.interpretar(item(cidade="Ilha de Itamaracá"), C, {"cidade": "Ilha de Itamaracá", "estado": "PE"})["confere"] is True
    assert svc.interpretar(item(cidade="Cuiabá", uf="BR-MT"), C, {"cidade": "CUIABA", "estado": "MT"})["confere"] is True  # sem acento/maiúsculas


# ───────── Processamento: gravar, preservar, cache ─────────
def test_localizada_grava_coordenadas_e_status(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(), i) == "localizado"
    with owner_engine.begin() as db:
        e = linha(db, i)
    assert (e["latitude"], e["longitude"], e["geocode_status"], e["geocode_precisao"]) == (-7.94, -34.87, "localizado", "endereco")
    assert e["geocoded_at"] and "Rua Marcondes" in e["geocode_endereco_usado"] and e["geocode_desatualizada"] == 0


def test_so_a_rua_e_localizado_aproximado_e_cidade_divergente_vira_revisao(owner_engine):
    with owner_engine.begin() as db:
        a, b = emp(db, "A"), emp(db, "B", rua="Rua Outra")
    geo = FakeGeo(lambda c: [item(house=None, rank=26, addresstype="road")] if "100" not in c.get("street", "") else [])
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, a) == "localizado"      # o OSM quase nunca tem o número: pela rua já vale
    with owner_engine.begin() as db:
        assert "Aproximada pela rua" in linha(db, a)["geocode_erro"]
    geo2 = FakeGeo(lambda c: [item(cidade="Recife")])
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo2, b) == "revisao"
    with owner_engine.begin() as db:
        assert linha(db, a)["geocode_precisao"] == "rua" and linha(db, a)["latitude"] is not None
        assert linha(db, b)["geocode_status"] == "revisao"


def test_nao_encontrada_continua_cadastrada(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda c: []), i) == "nao_encontrado"
    with owner_engine.begin() as db:
        e = linha(db, i)
    assert e["geocode_status"] == "nao_encontrado" and e["latitude"] is None and e["geocode_erro"] and e["nome"] == "E"


def test_cascata_tenta_proxima_consulta_se_a_primeira_falha(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    geo = FakeGeo(lambda c: [] if c.get("street", "").startswith("100") else [item(house=None, rank=26, addresstype="road")])
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, i) == "localizado"
    assert len(geo.chamadas) == 2


def test_servico_fora_do_ar_nao_muda_nada_e_nao_cacheia(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda c: GeocoderIndisponivel("x")), i) == "indisponivel"
    with owner_engine.begin() as db:
        assert linha(db, i)["geocode_status"] == "pendente" and linha(db, i)["geocode_tentativas"] == 0
        assert db.execute(text("SELECT count(*) FROM geocode_cache")).scalar_one() == 0


def test_cache_evita_consulta_repetida_inclusive_negativa(owner_engine):
    with owner_engine.begin() as db:
        a, b = emp(db, "A"), emp(db, "B")                      # mesmo endereço
        c, d = emp(db, "C", rua="Rua Inexistente"), emp(db, "D", rua="Rua Inexistente")
    geo = FakeGeo(lambda cs: [] if "Inexistente" in cs.get("street", "") else [item()])
    with owner_engine.connect() as db:
        for i in (a, b, c, d):
            svc.processar_empresa(db, geo, i)
    ruas = [x.get("street") for x in geo.chamadas]
    assert ruas.count("100 Rua Marcondes") == 1 and ruas.count("100 Rua Inexistente") == 1 and ruas.count("Rua Inexistente") == 1


def test_endereco_alterado_durante_a_consulta_nao_grava_resultado_velho(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)

    def mexe(c):
        with owner_engine.begin() as db:
            db.execute(text("UPDATE empresas SET rua='Rua NOVA' WHERE id=:i"), {"i": i})
        return [item()]
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(mexe), i) == "pulada"
    with owner_engine.begin() as db:
        assert linha(db, i)["latitude"] is None and linha(db, i)["geocode_status"] == "pendente"


def test_localizacao_anterior_preservada_ate_haver_uma_nova_valida(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db, latitude=-8.0, longitude=-34.9, geocode_status="localizado", geocode_desatualizada=1, geocode_precisao="endereco")
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda c: GeocoderIndisponivel("x")), i) == "indisponivel"
    with owner_engine.begin() as db:
        e = linha(db, i)
        assert (e["latitude"], e["longitude"], e["geocode_desatualizada"]) == (-8.0, -34.9, 1)
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, FakeGeo(lambda c: []), i) == "revisao"       # não achou o novo endereço
    with owner_engine.begin() as db:
        e = linha(db, i)
        assert (e["latitude"], e["longitude"]) == (-8.0, -34.9) and e["geocode_status"] == "revisao" and e["geocode_erro"]
    with owner_engine.connect() as db:
        svc.esquecer_negativos(db, svc.carregar(db, i))     # (o pedido de nova geocodificação faz isto)
        assert svc.processar_empresa(db, FakeGeo(lambda c: [item(lat=-7.5, lon=-34.8)]), i) == "localizado"
    with owner_engine.begin() as db:
        e = linha(db, i)
        assert (e["latitude"], e["longitude"], e["geocode_desatualizada"]) == (-7.5, -34.8, 0)  # só agora troca


def test_resposta_negativa_expira_e_pode_ser_esquecida(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        svc.processar_empresa(db, FakeGeo(lambda c: []), i)
    geo = FakeGeo()
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, i) == "nao_encontrado" and geo.chamadas == []     # cache negativo vale
    with owner_engine.begin() as db:
        db.execute(text("UPDATE geocode_cache SET criado_em='2000-01-01 00:00:00.000'"))        # venceu
    with owner_engine.connect() as db:
        assert svc.processar_empresa(db, geo, i) == "localizado" and geo.chamadas


def test_retentar_falhas_consulta_o_servico_de_novo(owner_engine):
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        svc.processar_lote(db, FakeGeo(lambda c: []), 5)
    geo = FakeGeo()
    with owner_engine.connect() as db:
        assert svc.processar_lote(db, geo, 5, retentar_falhas=True)["localizado"] == 1 and geo.chamadas


# ───────── Lote e trava ─────────
def test_lote_processa_so_pendentes_e_desatualizadas(owner_engine):
    with owner_engine.begin() as db:
        p = emp(db, "pendente")
        d = emp(db, "desatualizada", latitude=-8.0, longitude=-34.9, geocode_status="pendente", geocode_desatualizada=1)  # endereço editado
        ok = emp(db, "ok", latitude=-8.1, longitude=-34.9, geocode_status="localizado")
        nf = emp(db, "falhou", geocode_status="nao_encontrado")
        ex = emp(db, "excluida", deleted_at="2026-01-01 00:00:00.000")
    geo = FakeGeo()
    with owner_engine.connect() as db:
        r = svc.processar_lote(db, geo, 25)
    assert r["localizado"] == 2
    with owner_engine.begin() as db:
        assert linha(db, ok)["latitude"] == -8.1 and linha(db, nf)["latitude"] is None and linha(db, ex)["latitude"] is None
    with owner_engine.connect() as db:
        assert svc.processar_lote(db, geo, 25, retentar_falhas=True)["localizado"] == 1   # agora a "não encontrada"


def test_lote_para_apos_erros_seguidos(owner_engine):
    with owner_engine.begin() as db:
        for n in range(8):
            emp(db, f"E{n}", rua=f"Rua {n}")
    geo = FakeGeo(lambda c: GeocoderIndisponivel("x"))
    with owner_engine.connect() as db:
        r = svc.processar_lote(db, geo, 25)
    assert r["interrompido"] is True and len(geo.chamadas) == 3 and r["indisponivel"] == 3


def test_trava_so_um_geocodificador_por_vez(owner_engine):
    with owner_engine.connect() as a, owner_engine.connect() as b:
        assert svc.adquirir(a, "api") is True
        assert svc.adquirir(b, "cli") is False
        assert svc.adquirir(a, "api") is True               # quem tem a trava renova
        assert svc.processar_lote(b, FakeGeo(), 5, holder="cli") == {"ocupado": True}
        svc.liberar(a, "api")
        assert svc.adquirir(b, "cli") is True
        owner = owner_engine
    with owner.begin() as db:
        db.execute(text("UPDATE geocode_lease SET expires_at='2000-01-01 00:00:00.000'"))   # travou e morreu: expira sozinha
    with owner.connect() as c:
        assert svc.adquirir(c, "outro") is True


def test_trava_e_liberada_mesmo_se_der_erro_inesperado(owner_engine):
    with owner_engine.begin() as db:
        emp(db)
    with owner_engine.connect() as db:
        with pytest.raises(RuntimeError):
            svc.processar_lote(db, FakeGeo(lambda c: (_ for _ in ()).throw(RuntimeError("bug"))), 5)
        assert svc.adquirir(db, "depois") is True


# ───────── API ─────────
@pytest.fixture
def geo_falso(monkeypatch):
    geo = FakeGeo()
    monkeypatch.setattr(svc, "criar_geocoder", lambda: geo)
    return geo


def test_lote_via_api_so_admin_e_valida_limite(login, owner_engine, geo_falso):
    c, h = login("adm", "admin")
    with owner_engine.begin() as db:
        for n in range(3):
            emp(db, f"E{n}", rua=f"Rua {n}")
    for lim in (0, 26, -1, "x"):
        assert c.post("/api/geocodificacao/lote", json={"limite": lim}, headers=h).status_code == 422
    assert c.post("/api/geocodificacao/lote", json={"limite": 2, "extra": 1}, headers=h).status_code == 422
    r = c.post("/api/geocodificacao/lote", json={"limite": 2}, headers=h).json()
    assert r["localizado"] == 2 and r["pendente"] == 1 and r["total"] == 3
    assert c.post("/api/geocodificacao/lote", json={"limite": 25}, headers=h).json()["pendente"] == 0


@pytest.mark.parametrize("papel", ["operador", "visualizador"])
def test_lote_proibido_para_nao_admin(login, papel, geo_falso):
    c, h = login("user", papel)
    assert c.post("/api/geocodificacao/lote", json={}, headers=h).status_code == 403
    assert geo_falso.chamadas == []


def test_lote_ocupado_409_e_config_invalida_503_sem_detalhes(login, owner_engine, monkeypatch, geo_falso):
    c, h = login("adm", "admin")
    with owner_engine.begin() as db:
        emp(db)
        db.execute(text("UPDATE geocode_lease SET holder='cli-1', expires_at=:e"), {"e": "2999-01-01 00:00:00.000"})
    assert c.post("/api/geocodificacao/lote", json={}, headers=h).status_code == 409
    with owner_engine.begin() as db:
        db.execute(text("UPDATE geocode_lease SET holder=NULL, expires_at=NULL"))
    monkeypatch.setattr(svc, "criar_geocoder", lambda: (_ for _ in ()).throw(GeocoderConfigInvalida("http://10.0.0.1 segredo")))
    r = c.post("/api/geocodificacao/lote", json={}, headers=h)
    assert r.status_code == 503 and "10.0.0.1" not in r.text and "segredo" not in r.text


def test_lote_exige_login_e_csrf(client, login, geo_falso):
    assert client.post("/api/geocodificacao/lote", json={}, headers=ORIGIN).status_code == 401
    c, h = login("adm", "admin")
    assert c.post("/api/geocodificacao/lote", json={}, headers=ORIGIN).status_code == 403


def test_resumo_para_qualquer_usuario_logado(client, login, owner_engine):
    assert client.get("/api/geocodificacao/resumo").status_code == 401
    c, h = login("viewer", "visualizador")
    with owner_engine.begin() as db:
        emp(db, "a", latitude=-8, longitude=-34.9, geocode_status="localizado")
        emp(db, "b")
        emp(db, "c", geocode_status="nao_encontrado")
    r = c.get("/api/geocodificacao/resumo").json()
    assert (r["localizado"], r["pendente"], r["nao_encontrado"], r["total"]) == (1, 1, 1, 3) and r["contato_configurado"] is False


def test_correcao_manual_valida_e_substitui_a_localizacao(login, owner_engine):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        i = emp(db, latitude=-8.0, longitude=-34.9, geocode_status="revisao", geocode_desatualizada=1)
    r = c.put(f"/api/empresas/{i}/localizacao", json={"latitude": -7.9407, "longitude": -34.8728}, headers=h)
    assert r.status_code == 200
    with owner_engine.begin() as db:
        e = linha(db, i)
    assert (e["latitude"], e["longitude"], e["geocode_status"], e["geocode_precisao"], e["geocode_desatualizada"]) == (-7.9407, -34.8728, "localizado", "manual", 0)
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM audit_log WHERE action='localizacao_corrigida_manualmente'")).scalar_one() == 1


@pytest.mark.parametrize("corpo", [{"latitude": 91, "longitude": -34}, {"latitude": -8, "longitude": 181}, {"latitude": 48.85, "longitude": 2.35},
                                   {"latitude": "x", "longitude": 1}, {"latitude": -8}, {"latitude": -8, "longitude": -34, "geocode_status": "localizado"},
                                   {"latitude": -8, "longitude": -34, "id": 5}, {}])
def test_correcao_manual_rejeita_valores_ruins_e_campos_extras(login, owner_engine, corpo):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        i = emp(db)
    assert c.put(f"/api/empresas/{i}/localizacao", json=corpo, headers=h).status_code == 422
    with owner_engine.begin() as db:
        assert linha(db, i)["latitude"] is None


def test_correcao_manual_rejeita_nan_e_infinito(login, owner_engine):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        i = emp(db)
    for corpo in (b'{"latitude": NaN, "longitude": -34}', b'{"latitude": -8, "longitude": Infinity}', b'{"latitude": -Infinity, "longitude": -34}'):
        r = c.put(f"/api/empresas/{i}/localizacao", content=corpo, headers={**h, "Content-Type": "application/json"})
        assert r.status_code == 422
    with owner_engine.begin() as db:
        assert linha(db, i)["latitude"] is None


def test_correcao_manual_permissoes_e_ids(login, owner_engine):
    c, h = login("viewer", "visualizador")
    with owner_engine.begin() as db:
        i = emp(db)
    ok = {"latitude": -8.0, "longitude": -34.9}
    assert c.put(f"/api/empresas/{i}/localizacao", json=ok, headers=h).status_code == 403
    assert c.post(f"/api/empresas/{i}/geocodificar", headers=h).status_code == 403
    c2, h2 = login("oper", "operador")
    assert c2.put("/api/empresas/99999/localizacao", json=ok, headers=h2).status_code == 404
    assert c2.put("/api/empresas/abc/localizacao", json=ok, headers=h2).status_code == 422
    assert c2.post("/api/empresas/99999/geocodificar", headers=h2).status_code == 404


def test_pedir_nova_geocodificacao_ignora_resposta_negativa_antiga(login, owner_engine, monkeypatch):
    c, h = login("oper", "operador")
    with owner_engine.begin() as db:
        i = emp(db)
    with owner_engine.connect() as db:
        svc.processar_empresa(db, FakeGeo(lambda cs: []), i)
    geo = FakeGeo()
    monkeypatch.setattr(svc, "criar_geocoder", lambda: geo)
    c.post(f"/api/empresas/{i}/geocodificar", headers=h)
    adm, ha = login("adm", "admin")
    adm.post("/api/geocodificacao/lote", json={}, headers=ha)
    with owner_engine.begin() as db:
        assert linha(db, i)["geocode_status"] == "localizado"


def test_pedir_nova_geocodificacao_preserva_a_localizacao_atual(login, owner_engine, geo_falso):
    c, h = login("adm", "admin")
    with owner_engine.begin() as db:
        i = emp(db, latitude=-8.0, longitude=-34.9, geocode_status="localizado")
    assert c.post(f"/api/empresas/{i}/geocodificar", headers=h).json()["geocode_status"] == "pendente"
    with owner_engine.begin() as db:
        e = linha(db, i)
        assert (e["latitude"], e["geocode_desatualizada"]) == (-8.0, 1)
    c.post("/api/geocodificacao/lote", json={"limite": 5}, headers=h)
    with owner_engine.begin() as db:
        assert linha(db, i)["latitude"] == -7.94 and linha(db, i)["geocode_desatualizada"] == 0


# ───────── CLI ─────────
def test_cli_dry_run_nao_grava_nada(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        i = emp(db)
    linhas = []
    assert main(["--dry-run", "--amostra", "5"], geo=FakeGeo(), saida=linhas.append) == 0
    assert any("localizada" in x for x in linhas)
    with owner_engine.begin() as db:
        assert linha(db, i)["latitude"] is None and db.execute(text("SELECT count(*) FROM geocode_cache")).scalar_one() == 0


def test_cli_processa_tudo_e_pode_ser_repetido(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        for n in range(30):
            emp(db, f"E{n}", rua=f"Rua {n}")
    geo = FakeGeo()
    assert main([], geo=geo, saida=lambda *_: None) == 0
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas WHERE geocode_status='localizado'")).scalar_one() == 30
    n = len(geo.chamadas)
    assert main([], geo=geo, saida=lambda *_: None) == 0 and len(geo.chamadas) == n   # nada pendente: nenhuma consulta nova


def test_cli_retoma_apos_servico_cair(owner_engine):
    from scripts.geocode import main
    with owner_engine.begin() as db:
        for n in range(10):
            emp(db, f"E{n}", rua=f"Rua {n}")
    falhou = {"sim": True}
    geo = FakeGeo(lambda c: GeocoderIndisponivel("x") if falhou["sim"] else [item()])
    assert main([], geo=geo, saida=lambda *_: None) == 1
    falhou["sim"] = False
    assert main([], geo=geo, saida=lambda *_: None) == 0
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM empresas WHERE geocode_status='localizado'")).scalar_one() == 10
