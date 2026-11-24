"""Regras estruturais: valem mesmo que backend/frontend falhem (estão no próprio banco)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


def _emp(c, nome="EMPRESA X", **kw):
    cols = {"nome": nome, **kw}
    keys = ", ".join(cols)
    vals = ", ".join(f":{k}" for k in cols)
    return c.execute(text(f"INSERT INTO empresas ({keys}) VALUES ({vals}) RETURNING id"), cols).scalar_one()


def test_chaves_estrangeiras_ligadas(owner_engine):
    with owner_engine.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_cnpj_unique_no_banco(owner_engine):
    with owner_engine.begin() as c:
        _emp(c, cnpj="12884495000148")
    with pytest.raises(IntegrityError):
        with owner_engine.begin() as c:
            _emp(c, nome="OUTRA", cnpj="12884495000148")


def test_cnpj_so_aceita_14_digitos_normalizados(owner_engine):
    for bad in ["12.884.495/0001-48", "1288449500014", "abcdefghijklmn", "1288449500014A"]:
        with pytest.raises(IntegrityError):
            with owner_engine.begin() as c:
                _emp(c, cnpj=bad)


def test_varias_empresas_sem_cnpj_permitidas(owner_engine):
    with owner_engine.begin() as c:
        _emp(c, "A")
        _emp(c, "B")


def test_id_interno_gerado_pelo_banco_e_nunca_reaproveitado(owner_engine):
    with owner_engine.begin() as c:
        a, b = _emp(c, "A"), _emp(c, "B")
        c.execute(text("DELETE FROM empresas WHERE id=:i"), {"i": b})
        d = _emp(c, "D")
    assert b == a + 1 and d == b + 1


def test_numero_aceita_texto_livre(owner_engine):
    valores = ["10", "10A", "10-B", "100/102", "S/N", "Loja 01", "Fundos"]
    with owner_engine.begin() as c:
        for n in valores:
            _emp(c, f"E {n}", numero=n)
        got = c.execute(text("SELECT numero FROM empresas ORDER BY id")).scalars().all()
    assert got == valores and all(isinstance(g, str) for g in got)


def test_cep_e_telefone_texto_com_zero_a_esquerda(owner_engine):
    with owner_engine.begin() as c:
        i = _emp(c, cep="01001000", celular="08199558322")
        cep, cel = c.execute(text("SELECT cep, celular FROM empresas WHERE id=:i"), {"i": i}).one()
    assert (cep, cel) == ("01001000", "08199558322") and isinstance(cep, str)


def test_cep_e_telefone_rejeitam_lixo(owner_engine):
    for kw in [{"cep": "5300-090"}, {"cep": "123456789"}, {"celular": "abc"}, {"fone": "123"}]:
        with pytest.raises(IntegrityError):
            with owner_engine.begin() as c:
                _emp(c, **kw)


def test_coordenadas_persistidas_no_banco(owner_engine):
    with owner_engine.begin() as c:
        i = _emp(c, latitude=-7.9407, longitude=-34.8728, geocode_status="localizado")
        lat, lon, st = c.execute(text("SELECT latitude, longitude, geocode_status FROM empresas WHERE id=:i"), {"i": i}).one()
    assert (lat, lon, st) == (-7.9407, -34.8728, "localizado")


def test_lat_lon_validas_e_juntas(owner_engine):
    for kw in [{"latitude": 95, "longitude": 0}, {"latitude": 0, "longitude": 200}, {"latitude": 1.0}]:
        with pytest.raises(IntegrityError):
            with owner_engine.begin() as c:
                _emp(c, **kw)


def test_status_de_geocodificacao_so_aceita_valores_validos(owner_engine):
    with pytest.raises(IntegrityError):
        with owner_engine.begin() as c:
            _emp(c, geocode_status="qualquer")


def test_busca_por_area_usa_indice(owner_engine):
    with owner_engine.begin() as c:
        for k in range(300):
            _emp(c, f"E{k}", latitude=-7.9 - k * 0.0005, longitude=-34.87 + k * 0.0005)
        plan = " ".join(str(r) for r in c.execute(text(
            "EXPLAIN QUERY PLAN SELECT id FROM empresas WHERE latitude BETWEEN -8.0 AND -7.9 AND longitude BETWEEN -34.9 AND -34.8")))
        n = c.execute(text("SELECT count(*) FROM empresas WHERE latitude BETWEEN -8.0 AND -7.9 "
                           "AND longitude BETWEEN -34.9 AND -34.8")).scalar_one()
    assert "empresas_geo_idx" in plan and n > 0


def test_historico_e_imutavel(owner_engine, make_user):
    uid = make_user()
    with owner_engine.begin() as c:
        eid = _emp(c)
        c.execute(text("INSERT INTO interacoes (empresa_id, usuario_id, tipo, descricao) VALUES (:e,:u,'visita','ok')"),
                  {"e": eid, "u": uid})
    for sql in ["DELETE FROM interacoes", "UPDATE interacoes SET descricao='x'"]:
        with pytest.raises(DBAPIError):
            with owner_engine.begin() as c:
                c.execute(text(sql))


def test_auditoria_e_imutavel(owner_engine):
    with owner_engine.begin() as c:
        c.execute(text("INSERT INTO audit_log (action) VALUES ('x')"))
    for sql in ["DELETE FROM audit_log", "UPDATE audit_log SET action='y'"]:
        with pytest.raises(DBAPIError):
            with owner_engine.begin() as c:
                c.execute(text(sql))


def test_historico_sobrevive_a_mudanca_de_situacao(owner_engine, make_user):
    uid = make_user()
    with owner_engine.begin() as c:
        eid = _emp(c)
        s1 = c.execute(text("SELECT id FROM situacoes WHERE nome='Ativa'")).scalar_one()
        s2 = c.execute(text("SELECT id FROM situacoes WHERE nome='Visitada'")).scalar_one()
        c.execute(text("INSERT INTO interacoes (empresa_id, usuario_id, tipo, descricao, situacao_resultante_id) "
                       "VALUES (:e,:u,'visita','1',:s)"), {"e": eid, "u": uid, "s": s1})
        c.execute(text("UPDATE empresas SET situacao_id=:s WHERE id=:e"), {"s": s2, "e": eid})
        assert c.execute(text("SELECT count(*) FROM interacoes WHERE empresa_id=:e"), {"e": eid}).scalar_one() == 1


def test_empresa_com_historico_nao_pode_ser_apagada(owner_engine, make_user):
    uid = make_user()
    with owner_engine.begin() as c:
        eid = _emp(c)
        c.execute(text("INSERT INTO interacoes (empresa_id, usuario_id, tipo, descricao) VALUES (:e,:u,'outro','x')"),
                  {"e": eid, "u": uid})
    with pytest.raises(IntegrityError):
        with owner_engine.begin() as c:
            c.execute(text("DELETE FROM empresas WHERE id=:e"), {"e": eid})


def test_relacionamentos_por_id_invalidos_sao_rejeitados(owner_engine):
    with pytest.raises(IntegrityError):
        with owner_engine.begin() as c:
            _emp(c, situacao_id=99999)


def test_situacoes_iniciais_vem_do_banco_e_sao_editaveis(owner_engine):
    with owner_engine.begin() as c:
        nomes = c.execute(text("SELECT nome FROM situacoes ORDER BY ordem")).scalars().all()
        assert {"Ativa", "Inativa", "Em acompanhamento", "Visitada", "Contato realizado", "Sem contato", "Pendente"} <= set(nomes)
        c.execute(text("INSERT INTO situacoes (nome) VALUES ('Nova')"))
        c.execute(text("UPDATE situacoes SET ativa=0 WHERE nome='Nova'"))


def test_nomes_de_categoria_unicos_sem_diferenciar_maiusculas(owner_engine):
    with owner_engine.begin() as c:
        c.execute(text("INSERT INTO categorias (nome) VALUES ('Padaria')"))
    with pytest.raises(IntegrityError):
        with owner_engine.begin() as c:
            c.execute(text("INSERT INTO categorias (nome) VALUES ('padaria')"))


def test_init_db_e_idempotente_e_nao_apaga_dados(owner_engine):
    from app.schema import init_db
    with owner_engine.begin() as c:
        _emp(c, "GUARDADA", cnpj="12884495000148")
    assert init_db(owner_engine) is False
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM empresas")).scalar_one() == 1


def test_migracao_do_banco_antigo_preserva_dados(tmp_path):
    """Quem já tinha o banco da versão anterior atualiza sem perder nada."""
    import sqlite3
    from sqlalchemy import create_engine
    from app.schema import SCHEMA_SQL, init_db
    caminho = tmp_path / "antigo.db"
    cx = sqlite3.connect(caminho)
    cx.executescript(SCHEMA_SQL)
    cx.execute("INSERT INTO empresas (nome, cnpj) VALUES ('GUARDADA', '12884495000148')")
    cx.commit(); cx.close()
    e = create_engine(f"sqlite:///{caminho}")
    assert init_db(e) is True and init_db(e) is False
    with e.connect() as c:
        assert c.execute(text("SELECT nome, precisa_revisao, motivos_revisao FROM empresas")).one() == ("GUARDADA", 0, "[]")
        assert c.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars().all() == [1, 2, 3, 4, 5]
