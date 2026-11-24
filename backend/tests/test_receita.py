"""Consulta da situação cadastral (ativa / inativa / inapta) na Receita Federal. A rede é sempre simulada nos testes."""
import io
import json
import urllib.error

import pytest
from sqlalchemy import text

from app.modules.receita import client
from tests.test_importacao import C1

ORIGEM = {"Origin": "http://localhost:5173"}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def abrir_com(dados, capturar=None):
    def _abrir(req, timeout=None):
        if capturar is not None:
            capturar.append(req.full_url)
        if isinstance(dados, Exception):
            raise dados
        return Resp(dados if isinstance(dados, bytes) else json.dumps(dados).encode())
    return _abrir


URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"


@pytest.fixture(autouse=True)
def _dns(monkeypatch):  # sem rede: a validação de destino não pode depender de DNS real
    monkeypatch.setattr("app.modules.geocoding.client.socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("200.1.2.3", 443))])


# ───────── classificação (o que vira "ativa", "inativa" ou "inapta") ─────────
@pytest.mark.parametrize("dados,esperado", [
    ({"situacao_cadastral": 2, "descricao_situacao_cadastral": "ATIVA"}, ("ativa", "ATIVA")),
    ({"situacao_cadastral": 4, "descricao_situacao_cadastral": "INAPTA"}, ("inapta", "INAPTA")),
    ({"situacao_cadastral": 8, "descricao_situacao_cadastral": "BAIXADA"}, ("inativa", "BAIXADA")),
    ({"situacao_cadastral": 3, "descricao_situacao_cadastral": "SUSPENSA"}, ("inativa", "SUSPENSA")),
    ({"situacao_cadastral": 1, "descricao_situacao_cadastral": "NULA"}, ("inativa", "NULA")),
    ({"descricao_situacao_cadastral": "ativa"}, ("ativa", "ATIVA")),            # só o texto
    ({"situacao_cadastral": 2}, ("ativa", "ATIVA")),                            # só o código
])
def test_classificar(dados, esperado):
    r = client.classificar(dados)
    assert (r.situacao, r.descricao) == esperado


@pytest.mark.parametrize("dados", [None, [], "ATIVA", {}, {"situacao_cadastral": 99}, {"descricao_situacao_cadastral": "QUALQUER"},
                                   {"situacao_cadastral": 2, "descricao_situacao_cadastral": "BAIXADA"}, {"situacao_cadastral": True}])
def test_classificar_recusa_resposta_estranha(dados):
    with pytest.raises(client.ReceitaIndisponivel):
        client.classificar(dados)


# ───────── cliente HTTP ─────────
def test_cliente_monta_url_so_com_cnpj_validado_e_devolve_apenas_a_situacao():
    vistas = []
    rico = {"cnpj": C1, "razao_social": "EMPRESA SECRETA LTDA", "qsa": [{"nome_socio": "FULANO"}], "ddd_telefone_1": "8733334444",
            "situacao_cadastral": 2, "descricao_situacao_cadastral": "ATIVA"}
    r = client.consultar(C1, URL, abrir=abrir_com(rico, vistas))
    assert vistas == [f"https://brasilapi.com.br/api/cnpj/v1/{C1}"]
    assert r == client.Situacao("ativa", "ATIVA")
    assert set(vars(r)) == {"situacao", "descricao"}  # nada de razão social, sócios, telefone...


def test_cliente_recusa_cnpj_que_nao_e_so_digitos():
    for ruim in ("1234", "../../etc/passwd", "12345678000190/x", "1234567800019a"):
        with pytest.raises(ValueError):
            client.consultar(ruim, URL, abrir=abrir_com({}))


@pytest.mark.parametrize("url", ["http://brasilapi.com.br/api/{cnpj}", "https://localhost/{cnpj}", "https://u:p@brasilapi.com.br/{cnpj}", "https://brasilapi.com.br/semmodelo"])
def test_cliente_recusa_destino_inseguro_ou_mal_configurado(url):
    with pytest.raises(client.ReceitaIndisponivel):
        client.consultar(C1, url, abrir=abrir_com({}))


def test_cliente_bloqueia_destino_que_resolve_para_rede_interna(monkeypatch):
    monkeypatch.setattr("app.modules.geocoding.client.socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(client.ReceitaIndisponivel):
        client.consultar(C1, URL, abrir=abrir_com({}))


def _http(code):
    return urllib.error.HTTPError("https://x", code, "x", {}, io.BytesIO(b"corpo secreto do servidor"))


def test_cliente_erros_http():
    for cod in (404, 400):
        with pytest.raises(client.CnpjNaoEncontrado):
            client.consultar(C1, URL, abrir=abrir_com(_http(cod)))
    for cod in (429, 500, 503, 302):
        with pytest.raises(client.ReceitaIndisponivel) as e:
            client.consultar(C1, URL, abrir=abrir_com(_http(cod)))
        assert "secreto" not in str(e.value)
    for exc in (TimeoutError(), urllib.error.URLError("falhou"), OSError()):
        with pytest.raises(client.ReceitaIndisponivel):
            client.consultar(C1, URL, abrir=abrir_com(exc))


def test_cliente_resposta_invalida_ou_grande_demais():
    for corpo in (b"<html>", b"[1,2]", b"\xff\xfe"):
        with pytest.raises(client.ReceitaIndisponivel):
            client.consultar(C1, URL, abrir=abrir_com(corpo))
    with pytest.raises(client.ReceitaIndisponivel):
        client.consultar(C1, URL, abrir=abrir_com(b"{" + b" " * (300 * 1024) + b"}"))


# ───────── endpoint ─────────
@pytest.fixture
def falso(monkeypatch):
    """Substitui a chamada externa; guarda quantas vezes foi chamada."""
    est = {"n": 0, "resp": client.Situacao("ativa", "ATIVA")}

    def _consultar(cnpj, url, timeout=8.0, abrir=None):
        est["n"] += 1
        if isinstance(est["resp"], Exception):
            raise est["resp"]
        return est["resp"]
    monkeypatch.setattr("app.modules.receita.router.client.consultar", _consultar)
    return est


def test_exige_login():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get(f"/api/receita/situacao/{C1}", headers=ORIGEM).status_code == 401


@pytest.mark.parametrize("papel", ["visualizador", "operador", "admin"])
def test_qualquer_perfil_logado_consulta(login, falso, papel):
    c, _ = login("usuario_" + papel, papel)
    r = c.get(f"/api/receita/situacao/{C1}")
    assert r.status_code == 200, r.text
    assert r.json() == {"cnpj": C1, "situacao": "ativa", "descricao": "ATIVA", "consultado_em": r.json()["consultado_em"], "fonte": "Receita Federal", "do_cache": False}
    assert r.headers["cache-control"] == "no-store"


def test_aceita_cnpj_formatado_e_recusa_invalido(login, falso):
    c, _ = login()
    formatado = C1[:2] + "." + C1[2:5] + "." + C1[5:8] + "-" + C1[8:12] + "-" + C1[12:]
    r = c.get("/api/receita/situacao/" + formatado)
    assert r.status_code == 200 and r.json()["cnpj"] == C1 and falso["n"] == 1
    for ruim in ("123", "11111111111111", "abc", "12345678000190"):
        assert c.get(f"/api/receita/situacao/{ruim}").status_code == 422, ruim
    assert falso["n"] == 1  # CNPJ inválido nunca chega ao serviço externo


@pytest.mark.parametrize("resp,situacao", [(client.Situacao("inapta", "INAPTA"), "inapta"), (client.Situacao("inativa", "BAIXADA"), "inativa")])
def test_devolve_inapta_e_inativa(login, falso, resp, situacao):
    c, _ = login()
    falso["resp"] = resp
    assert c.get(f"/api/receita/situacao/{C1}").json()["situacao"] == situacao


def test_cache_evita_nova_consulta_externa_e_expira(login, falso, owner_engine):
    c, _ = login()
    assert c.get(f"/api/receita/situacao/{C1}").json()["do_cache"] is False
    r = c.get(f"/api/receita/situacao/{C1}").json()
    assert r["do_cache"] is True and falso["n"] == 1
    with owner_engine.begin() as db:  # cache vencido (> 24 h) => consulta de novo
        db.execute(text("UPDATE receita_cache SET consultado_em = '2000-01-01 00:00:00'"))
    falso["resp"] = client.Situacao("inapta", "INAPTA")
    r = c.get(f"/api/receita/situacao/{C1}").json()
    assert r["do_cache"] is False and r["situacao"] == "inapta" and falso["n"] == 2


def test_cache_guarda_so_cnpj_e_situacao(login, falso, owner_engine):
    c, _ = login()
    c.get(f"/api/receita/situacao/{C1}")
    with owner_engine.connect() as db:
        cols = [r[1] for r in db.execute(text("PRAGMA table_info(receita_cache)"))]
    assert cols == ["cnpj", "situacao", "descricao", "consultado_em"]


def test_nao_encontrado_e_servico_fora_do_ar_nao_vao_para_o_cache(login, falso, owner_engine):
    c, _ = login()
    falso["resp"] = client.CnpjNaoEncontrado()
    assert c.get(f"/api/receita/situacao/{C1}").status_code == 404
    falso["resp"] = client.ReceitaIndisponivel("detalhe interno")
    r = c.get(f"/api/receita/situacao/{C1}")
    assert r.status_code == 503 and "interno" not in r.text and "Tente novamente" in r.json()["detail"]
    with owner_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM receita_cache")).scalar_one() == 0


def test_limite_de_consultas_por_usuario(login, falso, owner_engine):
    c, _ = login()
    with owner_engine.begin() as db:
        uid = db.execute(text("SELECT id FROM users WHERE username='ana'")).scalar_one()
        for _ in range(60):
            db.execute(text("INSERT INTO rate_limit_events (bucket, key, created_at) VALUES ('receita', :k, strftime('%Y-%m-%d %H:%M:%f','now'))"), {"k": str(uid)})
    assert c.get(f"/api/receita/situacao/{C1}").status_code == 429
    assert falso["n"] == 0


def test_auditoria_nao_grava_o_cnpj(login, falso, owner_engine):
    c, _ = login()
    c.get(f"/api/receita/situacao/{C1}")
    with owner_engine.connect() as db:
        linhas = db.execute(text("SELECT details FROM audit_log WHERE action='consulta_receita'")).scalars().all()
    assert len(linhas) == 1 and C1 not in linhas[0] and "ativa" in linhas[0]


def test_desativada_por_configuracao(login, falso, monkeypatch):
    from app.config import get_settings
    c, _ = login()
    monkeypatch.setattr(get_settings(), "receita_ativo", False)
    assert c.get(f"/api/receita/situacao/{C1}").status_code == 503
    assert falso["n"] == 0
