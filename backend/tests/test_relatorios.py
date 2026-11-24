"""PDF das empresas da busca, relatório de atividades por data e dashboard."""
import io

import pytest
from pypdf import PdfReader
from sqlalchemy import text

from app.config import get_settings
from tests.test_mapa import E


def texto(r) -> str:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)


def uid(db, nome="ana"):
    return db.execute(text("SELECT id FROM users WHERE username = :u"), {"u": nome}).scalar_one()


def interacao(db, empresa_id, usuario_id, tipo="ligacao", desc="Contato feito", quando="2026-10-10 15:00:00.000"):
    db.execute(text("INSERT INTO interacoes (empresa_id, usuario_id, tipo, descricao, data_interacao) VALUES (:e, :u, :t, :d, :q)"),
               {"e": empresa_id, "u": usuario_id, "t": tipo, "d": desc, "q": quando})


@pytest.fixture
def cli(login):
    return login("ana", "operador")[0]


@pytest.fixture
def dados(cli, owner_engine):
    with owner_engine.begin() as db:
        a = E(db, "Padaria Alfa", "100")
        b = E(db, "Mercado Beta", "102", lat=-7.941, lon=-34.871)
        c = E(db, "Sem Mapa Ltda", "104", lat=None, lon=None)
        o = E(db, "Olinda Gama", "5", cidade="Olinda", rua="Rua do Sol", lat=-8.0, lon=-34.85)
        u = uid(db)
        interacao(db, a, u, "ligacao", "Primeira ligação à padaria", "2026-10-10 15:00:00.000")
        interacao(db, a, u, "visita", "Visita concluída com sucesso", "2026-10-12 12:00:00.000")
        interacao(db, b, u, "email", "E-mail enviado", "2026-10-12 13:00:00.000")
    return {"a": a, "b": b, "c": c, "o": o}


# ───────── PDF das empresas ─────────
def test_pdf_traz_exatamente_a_busca_atual_com_criterios_mapa_e_ultima_interacao(cli, dados):
    r = cli.get("/api/relatorios/empresas.pdf", params={"rua": "Rua Marcondes", "paridade": "pares"})
    t = texto(r)
    assert "Padaria Alfa" in t and "Mercado Beta" in t and "Sem Mapa Ltda" in t and "Olinda Gama" not in t
    assert "Rua Marcondes" in t and "Apenas números pares" in t
    assert "3 empresa(s) encontrada(s)" in t and "2 com localização" in t and "1 sem localização" in t
    assert "Visita concluída com sucesso" in t and "Primeira ligação" not in t  # só a ÚLTIMA interação
    assert "Sem interações registradas" in t
    assert "Mapa esquemático" in t
    assert 'filename="relatorio-empresas-' in r.headers["content-disposition"]


def test_pdf_ordena_por_endereco_quando_ha_filtro_de_rua(cli, dados):
    t = texto(cli.get("/api/relatorios/empresas.pdf", params={"rua": "Marcondes"}))
    assert t.index("Padaria Alfa") < t.index("Mercado Beta") < t.index("Sem Mapa")  # 100, 102, 104


def test_pdf_sem_resultados_e_limite_de_tamanho(cli, dados):
    assert cli.get("/api/relatorios/empresas.pdf", params={"rua": "Rua Inexistente"}).status_code == 404
    antigo = get_settings().max_pdf_empresas
    get_settings().max_pdf_empresas = 2
    try:
        r = cli.get("/api/relatorios/empresas.pdf")
        assert r.status_code == 422 and "Refine a busca" in r.json()["detail"]
    finally:
        get_settings().max_pdf_empresas = antigo


def test_pdf_nao_quebra_com_marcacao_nem_caracteres_estranhos_nos_dados(cli, owner_engine):
    with owner_engine.begin() as db:
        i = E(db, "<b>Negrito</b> & <font size=99>Cia <para>", "1")
        interacao(db, i, uid(db), desc="<script>alert(1)</script> 日本語 \x07 </b></para>")
    t = texto(cli.get("/api/relatorios/empresas.pdf"))
    assert "<b>Negrito</b>" in t and "<script>alert(1)</script>" in t  # aparece como TEXTO, não como marcação


def test_pdf_exige_login_e_aceita_visualizador(client, login):
    assert client.get("/api/relatorios/empresas.pdf").status_code == 401
    v = login("vera", "visualizador")[0]
    assert v.get("/api/relatorios/empresas.pdf").status_code in (200, 404)


def test_pdf_filtro_invalido_e_rejeitado_e_auditado(cli, dados, owner_engine):
    assert cli.get("/api/relatorios/empresas.pdf", params={"paridade": "x"}).status_code == 422
    assert cli.get("/api/relatorios/empresas.pdf", params={"campo": "nao_existe:1"}).status_code == 422
    assert cli.get("/api/relatorios/empresas.pdf").status_code == 200
    with owner_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM audit_log WHERE action = 'relatorio_pdf_empresas'")).scalar_one() == 1


def test_pdf_limite_de_geracoes_por_hora(cli, dados):
    for _ in range(30):
        assert cli.get("/api/relatorios/empresas.pdf", params={"rua": "Marcondes"}).status_code == 200
    assert cli.get("/api/relatorios/empresas.pdf", params={"rua": "Marcondes"}).status_code == 429


# ───────── Relatório de atividades por data ─────────
def test_atividades_resumo_e_filtros_por_periodo_tipo_e_usuario(cli, dados, owner_engine):
    r = cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-12"})
    j = r.json()
    assert r.status_code == 200 and j["resumo"]["total"] == 3 and j["resumo"]["empresas"] == 2
    assert j["resumo"]["por_tipo"] == {"ligacao": 1, "visita": 1, "email": 1}
    assert j["resumo"]["por_dia"] == {"2026-10-10": 1, "2026-10-12": 2}
    um_dia = cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-12", "fim": "2026-10-12"}).json()
    assert um_dia["resumo"]["total"] == 2
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-12", "tipo": "visita"}).json()["resumo"]["total"] == 1
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-12", "cidade": "Olinda"}).json()["resumo"]["total"] == 0
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-13", "fim": "2026-10-20"}).json()["resumo"]["total"] == 0


def test_atividades_usa_o_dia_local_de_recife_nas_bordas(cli, owner_engine):
    with owner_engine.begin() as db:
        e = E(db, "Borda", "1")
        u = uid(db)
        interacao(db, e, u, desc="noite 11/10 em Recife", quando="2026-10-12 02:30:00.000")   # 23:30 de 11/10 local
        interacao(db, e, u, desc="madrugada 12/10 em Recife", quando="2026-10-12 03:00:00.000")  # 00:00 de 12/10 local
    dia11 = cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-11", "fim": "2026-10-11"}).json()
    dia12 = cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-12", "fim": "2026-10-12"}).json()
    assert [i["descricao"] for i in dia11["itens"]] == ["noite 11/10 em Recife"]
    assert [i["descricao"] for i in dia12["itens"]] == ["madrugada 12/10 em Recife"]


def test_atividades_validacoes_de_periodo(cli):
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-12", "fim": "2026-10-10"}).status_code == 422
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2024-01-01", "fim": "2026-10-10"}).status_code == 422
    assert cli.get("/api/relatorios/atividades", params={"inicio": "abc", "fim": "2026-10-10"}).status_code == 422
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10"}).status_code == 422
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-11", "tipo": "xx"}).status_code == 422


def test_atividades_pdf_conteudo(cli, dados):
    t = texto(cli.get("/api/relatorios/atividades.pdf", params={"inicio": "2026-10-10", "fim": "2026-10-12"}))
    assert "Relatório de atividades" in t and "10/10/2026 a 12/10/2026" in t
    assert "3 atividade(s)" in t and "2 empresa(s) atendida(s)" in t
    assert "Primeira ligação à padaria" in t and "E-mail enviado" in t and "Visita" in t
    vazio = texto(cli.get("/api/relatorios/atividades.pdf", params={"inicio": "2026-01-01", "fim": "2026-01-02"}))
    assert "Nenhuma atividade registrada neste período" in vazio


def test_atividades_nao_mostram_empresas_excluidas(cli, dados, owner_engine):
    with owner_engine.begin() as db:
        db.execute(text("UPDATE empresas SET deleted_at = '2026-10-13 00:00:00.000' WHERE id = :i"), {"i": dados["b"]})
    assert cli.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-12"}).json()["resumo"]["total"] == 2


def test_atividades_exigem_login(client):
    assert client.get("/api/relatorios/atividades", params={"inicio": "2026-10-10", "fim": "2026-10-12"}).status_code == 401
    assert client.get("/api/relatorios/atividades.pdf", params={"inicio": "2026-10-10", "fim": "2026-10-12"}).status_code == 401
    assert client.get("/api/relatorios/usuarios").status_code == 401


def test_lista_de_usuarios_com_atividade(cli, dados):
    assert [u["username"] for u in cli.get("/api/relatorios/usuarios").json()] == ["ana"]


# ───────── Dashboard ─────────
def test_dashboard_totais_e_distribuicoes(cli, dados, owner_engine):
    j = cli.get("/api/dashboard").json()
    assert j["total"] == 4 and j["com_localizacao"] == 3 and j["sem_localizacao"] == 1
    assert {c["nome"]: c["total"] for c in j["por_cidade"]} == {"Paulista": 3, "Olinda": 1}
    assert len(j["empresas_recentes"]) == 4 and len(j["interacoes_recentes"]) == 3
    assert j["interacoes_recentes"][0]["usuario"] == "ana"


def test_dashboard_exige_login(client):
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_ignora_excluidas(cli, dados, owner_engine):
    with owner_engine.begin() as db:
        db.execute(text("UPDATE empresas SET deleted_at = '2026-10-13 00:00:00.000' WHERE id = :i"), {"i": dados["o"]})
    assert cli.get("/api/dashboard").json()["total"] == 3
