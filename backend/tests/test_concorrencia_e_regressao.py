"""Fase 6: concorrência, integridade e regressões de segurança dos pontos adicionados nas Fases 4–5."""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from app.modules.geocoding import service as gsvc
from tests.conftest import ORIGIN
from tests.test_mapa import E
from tests.test_relatorios import interacao, uid


def paralelo(fns):
    res, ts = [], [threading.Thread(target=lambda f=f: res.append(f())) for f in fns]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    return res


@pytest.fixture
def cli(login):
    return login("ana", "operador")


# ───────── concorrência ─────────
def test_edicoes_simultaneas_com_a_mesma_versao_so_uma_vence(login, make_user, owner_engine):
    c1, h1 = login("ana", "operador")
    with owner_engine.begin() as db:
        i = E(db, "Original", "1")
    from fastapi.testclient import TestClient
    from app.main import app
    clientes = []
    for nome in ("bia", "caio", "dora", "edu"):
        make_user(username=nome, role="operador")
        c = TestClient(app, base_url="http://localhost:5173", raise_server_exceptions=False)
        r = c.post("/api/auth/login", json={"username": nome, "password": "Senha-Forte-123"}, headers=ORIGIN)
        clientes.append((c, {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}, nome))
    v = c1.get(f"/api/empresas/{i}").json()["version"]
    res = paralelo([lambda c=c, h=h, n=n: c.patch(f"/api/empresas/{i}", json={"version": v, "nome": f"Editada {n}"}, headers=h).status_code for c, h, n in clientes])
    assert sorted(res) == [200, 409, 409, 409]
    with owner_engine.connect() as db:
        assert db.execute(text("SELECT version FROM empresas WHERE id=:i"), {"i": i}).scalar_one() == v + 1


def test_trava_do_geocodificador_so_um_dono_por_vez(owner_engine):
    def tenta(nome):
        with owner_engine.connect() as db:
            return gsvc.adquirir(db, nome, 30)
    res = paralelo([lambda n=n: tenta(f"h{n}") for n in range(8)])
    assert res.count(True) == 1


def test_interacoes_simultaneas_nao_se_perdem_nem_duplicam(cli, owner_engine):
    c, h = cli
    with owner_engine.begin() as db:
        i = E(db, "Atendida", "1")
    res = paralelo([lambda n=n: c.post(f"/api/empresas/{i}/interacoes", json={"tipo": "ligacao", "descricao": f"contato {n}"}, headers=h).status_code for n in range(12)])
    assert res == [201] * 12
    with owner_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM interacoes WHERE empresa_id=:i"), {"i": i}).scalar_one() == 12


def test_pdfs_simultaneos_do_mesmo_usuario_funcionam(cli, owner_engine):
    c, _ = cli
    with owner_engine.begin() as db:
        E(db, "A", "1")
    res = paralelo([lambda: c.get("/api/relatorios/empresas.pdf").status_code for _ in range(5)])
    assert res == [200] * 5


# ───────── injeção e entradas hostis nos relatórios/painel ─────────
PAYLOADS = ["' OR 1=1 --", "\"; DROP TABLE empresas; --", "%' UNION SELECT password_hash FROM users --", "\\", "%", "_", "\x00", "<script>x</script>", "a" * 5000]


@pytest.mark.parametrize("p", PAYLOADS)
def test_filtros_hostis_nao_quebram_nem_vazam(cli, owner_engine, p):
    c, _ = cli
    with owner_engine.begin() as db:
        E(db, "Normal", "1")
    for rota in ("/api/relatorios/empresas.pdf", "/api/mapa/empresas"):
        for campo in ("rua", "bairro", "cidade", "numero", "q"):
            r = c.get(rota, params={campo: p})
            assert r.status_code in (200, 404, 422), (rota, campo, r.status_code)
            assert b"password_hash" not in r.content
    r = c.get("/api/relatorios/atividades", params={"inicio": "2026-01-01", "fim": "2026-01-02", "cidade": p})
    assert r.status_code in (200, 422)
    with owner_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM empresas")).scalar_one() == 1


def test_cabecalhos_de_seguranca_nos_novos_endpoints(cli, owner_engine):
    c, _ = cli
    with owner_engine.begin() as db:
        E(db, "A", "1")
    for rota, params in (("/api/relatorios/empresas.pdf", {}), ("/api/dashboard", {}),
                         ("/api/relatorios/atividades", {"inicio": "2026-10-01", "fim": "2026-10-02"})):
        r = c.get(rota, params=params)
        assert r.headers["x-content-type-options"] == "nosniff", rota
        assert "default-src 'none'" in r.headers["content-security-policy"], rota
        assert r.headers.get("cache-control", "").startswith("no-store"), rota


def test_perfil_visualizador_nao_altera_nada_pelos_novos_endpoints(login):
    c, h = login("vera", "visualizador")
    for m in (c.post, c.put, c.patch, c.delete):
        assert m("/api/dashboard", headers=h).status_code in (404, 405)
        assert m("/api/relatorios/empresas.pdf", headers=h).status_code in (404, 405)


def test_pdf_nao_expoe_dados_de_empresa_excluida(cli, owner_engine):
    c, _ = cli
    with owner_engine.begin() as db:
        i = E(db, "Segredo Excluida", "1")
        interacao(db, i, uid(db), desc="conversa privada")
        db.execute(text("UPDATE empresas SET deleted_at='2026-10-01 00:00:00.000' WHERE id=:i"), {"i": i})
        E(db, "Visivel", "2")
    from tests.test_relatorios import texto
    t = texto(c.get("/api/relatorios/empresas.pdf"))
    assert "Segredo Excluida" not in t and "conversa privada" not in t and "Visivel" in t


# ───────── backup ─────────
def test_backup_consistente_com_verificacao_e_retencao(owner_engine, tmp_path):
    from scripts import backup
    with owner_engine.begin() as db:
        E(db, "Para backup", "1")
    destino = tmp_path / "bk"
    a = backup.fazer_backup(destino, manter=2, agora=datetime(2026, 10, 1, 8, 0, 0))
    backup.fazer_backup(destino, manter=2, agora=datetime(2026, 10, 2, 8, 0, 0))
    backup.fazer_backup(destino, manter=2, agora=datetime(2026, 10, 3, 8, 0, 0))
    assert not a.exists() and len(list(destino.glob("*.db"))) == 2  # a mais antiga foi removida
    novo = sorted(destino.glob("*.db"))[-1]
    con = sqlite3.connect(novo)
    assert con.execute("SELECT count(*) FROM empresas WHERE nome='Para backup'").fetchone()[0] == 1
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_backup_script_main(owner_engine, tmp_path):
    from scripts import backup
    saida = []
    assert backup.main(["--destino", str(tmp_path / "x")], saida.append) == 0 and "integridade verificada" in saida[0]
