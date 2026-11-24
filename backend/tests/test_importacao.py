import io
import json
import threading
import zipfile

import pytest
from openpyxl import Workbook
from sqlalchemy import text

from app.config import get_settings
from app.modules.companies.normalizers import cnpj_dv_valid
from tests.conftest import ORIGIN, PASSWORD

HEAD = ["Nome da Empresa", "Email", "Emails adicionais", "Celular", "Nome Fantasia", "CNPJ", "Fone", "Rua", "Número",
        "Complemento", "Bairro", "Cidade", "CEP", "Estado"]
CHAVES = ["nome", "email", "emails_adicionais", "celular", "nome_fantasia", "cnpj", "fone", "rua", "numero",
          "complemento", "bairro", "cidade", "cep", "estado"]
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def cnpj_ok(base12: str) -> str:
    for dv in range(100):
        c = base12 + f"{dv:02d}"
        if cnpj_dv_valid(c):
            return c
    raise AssertionError


C1, C2, C3 = cnpj_ok("128844950001"), cnpj_ok("027865580021"), cnpj_ok("075175360001")


def fmt(c):
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def L(**kw):
    base = {"nome": "EMPRESA A", "cnpj": fmt(C1), "rua": "Rua Marcondes", "numero": "100", "bairro": "Centro",
            "cidade": "Paulista - Pernambuco", "cep": "53401-000", "estado": "PE"}
    base.update(kw)
    return [base.get(k, "") for k in CHAVES]


def xlsx(rows, headers=HEAD) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
        for cel in ws[ws.max_row]:  # texto que começa com "=" digitado como TEXTO (não como fórmula)
            if isinstance(cel.value, str) and cel.value.startswith("=") and not getattr(cel, "_formula", False):
                cel.data_type = "s"
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def enviar(c, h, data, nome="clientes.xlsx", mime=MIME):
    return c.post("/api/importacoes", files={"arquivo": (nome, data, mime)}, headers=h)


def previa(c, h, rows, **kw):
    r = enviar(c, h, xlsx(rows, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def confirmar(c, h, imp, modo="novos"):
    return c.post(f"/api/importacoes/{imp['id']}/confirmar", json={"modo": modo}, headers=h)


def empresas(owner_engine, where="1=1"):
    with owner_engine.begin() as db:
        return [dict(r) for r in db.execute(text(f"SELECT * FROM empresas WHERE {where} ORDER BY id")).mappings().all()]  # noqa: S608


def zip_com(extra: dict[str, bytes] | None = None, base: bytes | None = None, flag=False) -> bytes:
    """Reconstrói um .xlsx válido acrescentando partes (para testar ZIPs maliciosos)."""
    src = zipfile.ZipFile(io.BytesIO(base or xlsx([L()])))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in src.infolist():
            z.writestr(i.filename, src.read(i.filename))
        for nome, conteudo in (extra or {}).items():
            zi = zipfile.ZipInfo(nome)
            if flag:
                zi.flag_bits |= 0x1
            z.writestr(zi, conteudo, zipfile.ZIP_DEFLATED)
    return out.getvalue()


# ───────── Prévia e importação básica ─────────
def test_previa_nao_grava_nada_e_mostra_contagens(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(), L(nome="B", cnpj=fmt(C2)), L(nome="C", cnpj="")])
    assert imp["status"] == "previa" and imp["resumo"]["novas"] == 3 and imp["resumo"]["novas_para_revisao"] == 1
    assert empresas(owner_engine) == []


def test_importar_novos_normaliza_cnpj_cidade_estado_e_preserva_texto(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(numero="10A", cep="01001-000", celular="(81) 99558-3225", fone="3433-1020")])
    r = confirmar(c, h, imp)
    assert r.status_code == 200 and r.json()["status"] == "concluida" and r.json()["resultado"]["criadas"] == 1
    e = empresas(owner_engine)[0]
    assert (e["cnpj"], e["cidade"], e["estado"], e["numero"], e["numero_base"]) == (C1, "Paulista", "PE", "10A", 10)
    assert (e["cep"], e["celular"], e["fone"]) == ("01001000", "81995583225", "34331020")
    assert e["geocode_status"] == "pendente" and e["latitude"] is None and e["importacao_id"] == imp["id"]
    assert e["precisa_revisao"] == 0 and e["created_by"] == 1


def test_numero_sn_e_textos_livres_preservados(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome=f"E{n}", cnpj="", numero=n) for n in ["S/N", "Loja 01", "100/102", "Fundos"]])
    confirmar(c, h, imp)
    got = {e["numero"]: e["numero_base"] for e in empresas(owner_engine)}
    assert got == {"S/N": None, "Loja 01": 1, "100/102": 100, "Fundos": None}


def test_cnpj_numerico_na_celula_e_zeros_perdidos(login, owner_engine):
    c, h = login("adm", "admin")
    perdido = int(C2[1:])  # o Excel guardou como número e perdeu o zero à esquerda
    imp = previa(c, h, [L(cnpj=int(C1)), L(nome="B", cnpj=perdido)])
    confirmar(c, h, imp)
    a, b = empresas(owner_engine)
    assert a["cnpj"] == C1
    assert b["cnpj"] is None and b["cnpj_original"] == str(perdido) and b["precisa_revisao"] == 1  # não inventa o zero


# ───────── Revisão: sem CNPJ / CNPJ ruim / dados duvidosos ─────────
def test_sem_cnpj_e_cnpj_incompleto_entram_como_novas_marcadas_para_revisao(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome="SEM", cnpj=""), L(nome="CPF", cnpj="198.827.424-91"), L(nome="DV", cnpj="12.884.495/0001-99"),
                        L(nome="OK", cnpj=fmt(C2))])
    assert imp["resumo"]["novas"] == 4 and imp["resumo"]["novas_para_revisao"] == 3 and imp["resumo"]["rejeitadas"] == 0
    confirmar(c, h, imp)
    por_nome = {e["nome"]: e for e in empresas(owner_engine)}
    assert por_nome["SEM"]["cnpj"] is None and por_nome["SEM"]["precisa_revisao"] == 1
    assert por_nome["CPF"]["cnpj"] is None and por_nome["CPF"]["cnpj_original"] == "198.827.424-91"
    assert por_nome["DV"]["cnpj_original"] == "12.884.495/0001-99" and por_nome["DV"]["precisa_revisao"] == 1
    assert por_nome["OK"]["precisa_revisao"] == 0
    assert "CNPJ" in json.loads(por_nome["CPF"]["motivos_revisao"])[0]


def test_dados_invalidos_nao_entram_mas_o_original_fica_no_motivo(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(cep="5365081", celular="123", email="isso nao e email", fone="3433-1020", cidade="Paulista", estado="XX")])
    confirmar(c, h, imp)
    e = empresas(owner_engine)[0]
    assert e["cep"] == "5365081" and e["celular"] is None and e["email"] is None and e["estado"] is None
    motivos = " ".join(json.loads(e["motivos_revisao"]))
    for trecho in ["CEP incompleto", "'123'", "isso", "XX"]:
        assert trecho in motivos
    assert e["precisa_revisao"] == 1


def test_endereco_incompleto_marca_revisao(login, owner_engine):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L(rua="", cidade="")]))
    assert "falta: rua, cidade" in empresas(owner_engine)[0]["motivos_revisao"]


def test_emails_principal_e_adicionais_misturados(login, owner_engine):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L(email="a@x.com; b@x.com", emails_adicionais="c@x.com, a@x.com ruim@")]))
    e = empresas(owner_engine)[0]
    assert e["email"] == "a@x.com" and json.loads(e["emails_adicionais"]) == ["b@x.com", "c@x.com"]


def test_estado_pela_coluna_ou_pela_cidade_e_conflito(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome="A", cnpj="", cidade="Olinda - Pernambuco", estado=""),
                        L(nome="B", cnpj="", cidade="Cuiabá - Mato Grosso", estado="MT"),
                        L(nome="C", cnpj="", cidade="Recife", estado="Pernambuco")])
    confirmar(c, h, imp)
    got = {e["nome"]: (e["cidade"], e["estado"]) for e in empresas(owner_engine)}
    assert got == {"A": ("Olinda", "PE"), "B": ("Cuiabá", "MT"), "C": ("Recife", "PE")}


def test_marcar_como_revisada_e_filtro(login, make_user):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L(nome="REV", cnpj=""), L(nome="OK", cnpj=fmt(C2))]))
    lista = c.get("/api/empresas?revisao=true").json()
    assert lista["total"] == 1 and lista["itens"][0]["nome"] == "REV" and lista["itens"][0]["motivos_revisao"]
    eid = lista["itens"][0]["id"]
    from fastapi.testclient import TestClient
    from app.main import app
    make_user("viewer", "visualizador")
    v = TestClient(app, base_url="http://localhost:5173")
    rv = v.post("/api/auth/login", json={"username": "viewer", "password": PASSWORD}, headers=ORIGIN)
    hv = {**ORIGIN, "X-CSRF-Token": rv.json()["csrf_token"]}
    assert v.post(f"/api/empresas/{eid}/revisado", headers=hv).status_code == 403
    r = c.post(f"/api/empresas/{eid}/revisado", headers=h)
    assert r.status_code == 200 and r.json()["precisa_revisao"] is False and r.json()["motivos_revisao"] == []
    assert c.post(f"/api/empresas/{eid}/revisado", headers=h).status_code == 200  # idempotente
    assert c.get("/api/empresas?revisao=true").json()["total"] == 0
    assert c.post("/api/empresas/99999/revisado", headers=h).status_code == 404
    v.close()


# ───────── Duplicatas: manter o registro MAIS COMPLETO ─────────
def test_duplicata_mantem_o_registro_mais_completo(login, owner_engine):
    c, h = login("adm", "admin")
    pobre = L(nome="PADARIA (pobre)", numero="", bairro="", cep="", celular="")
    rico = L(nome="PADARIA (rica)", numero="100", celular="81995583225", email="a@b.com", complemento="Loja 2")
    imp = previa(c, h, [pobre, rico])
    assert imp["resumo"]["novas"] == 1 and imp["resumo"]["duplicadas_descartadas"] == 1
    lin = c.get(f"/api/importacoes/{imp['id']}/linhas?acao=duplicada").json()["itens"][0]
    assert lin["linha"] == 2 and lin["mantida_linha"] == 3
    confirmar(c, h, imp)
    es = empresas(owner_engine)
    assert len(es) == 1 and es[0]["nome"] == "PADARIA (rica)" and es[0]["email"] == "a@b.com"


def test_duplicata_empate_mantem_a_primeira(login, owner_engine):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L(nome="PRIMEIRA"), L(nome="SEGUNDA")]))
    assert [e["nome"] for e in empresas(owner_engine)] == ["PRIMEIRA"]


def test_duplicatas_sem_cnpj_identicas_sao_descartadas(login):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome="X", cnpj=""), L(nome="x", cnpj="")])
    assert imp["resumo"]["novas"] == 1 and imp["resumo"]["duplicadas_descartadas"] == 1


def test_reimportar_o_mesmo_arquivo_nao_duplica_nada(login, owner_engine):
    c, h = login("adm", "admin")
    rows = [L(), L(nome="B", cnpj=fmt(C2)), L(nome="SEM", cnpj=""), L(nome="CPF", cnpj="19882742491")]
    confirmar(c, h, previa(c, h, rows), "ambos")
    antes = len(empresas(owner_engine))
    imp2 = previa(c, h, rows)
    assert imp2["resumo"]["novas"] == 0 and imp2["resumo"]["sem_alteracao"] == 2 and imp2["resumo"]["ja_existentes_sem_cnpj"] == 2
    r = confirmar(c, h, imp2, "ambos").json()["resultado"]
    assert r["criadas"] == 0 and r["atualizadas"] == 0 and len(empresas(owner_engine)) == antes


# ───────── Modos: cadastrar novos / atualizar existentes ─────────
def _base_existente(c, h, ow):
    adm_sit = {s["nome"]: s["id"] for s in c.get("/api/cadastros/situacoes").json()}
    e = c.post("/api/empresas", headers=h, json={"nome": "NOME ANTIGO", "cnpj": C1, "email": "velho@x.com", "rua": "Rua A", "numero": "1",
                                                   "cidade": "Olinda", "estado": "PE", "informacoes_adicionais": "NAO MEXER",
                                                   "situacao_id": adm_sit["Visitada"]}).json()
    with ow.begin() as db:
        db.execute(text("UPDATE empresas SET latitude=-8.0, longitude=-34.8, geocode_status='localizado' WHERE id=:i"), {"i": e["id"]})
    return e["id"]


def test_modo_atualizar_altera_so_o_que_veio_na_planilha(login, owner_engine):
    c, h = login("adm", "admin")
    eid = _base_existente(c, h, owner_engine)
    imp = previa(c, h, [L(nome="NOME NOVO", email="novo@x.com", rua="", numero="", cidade="Olinda - Pernambuco", celular="81995583225")])
    assert imp["resumo"]["para_atualizar"] == 1
    lin = c.get(f"/api/importacoes/{imp['id']}/linhas?acao=atualizar").json()["itens"][0]
    assert {"nome", "email", "celular"} <= set(lin["alteracoes"]) and lin["empresa_id"] == eid
    r = confirmar(c, h, imp, "atualizar").json()["resultado"]
    assert r["atualizadas"] == 1 and r["criadas"] == 0
    e = c.get(f"/api/empresas/{eid}").json()
    assert e["nome"] == "NOME NOVO" and e["email"] == "novo@x.com" and e["celular"] == "81995583225"
    assert "velho@x.com" in e["emails_adicionais"]                       # email antigo não se perde
    assert e["rua"] == "Rua A" and e["numero"] == "1"                    # célula vazia NÃO apaga dado existente
    assert e["informacoes_adicionais"] == "NAO MEXER" and e["situacao_nome"] == "Visitada"  # fora da planilha: intocado
    assert (e["latitude"], e["longitude"]) == (-8.0, -34.8) and e["version"] == 2


def test_atualizar_endereco_marca_geocodificacao_desatualizada_e_preserva_coordenadas(login, owner_engine):
    c, h = login("adm", "admin")
    eid = _base_existente(c, h, owner_engine)
    confirmar(c, h, previa(c, h, [L(rua="Rua NOVA", numero="99")]), "atualizar")
    e = c.get(f"/api/empresas/{eid}").json()
    assert e["rua"] == "Rua NOVA" and e["geocode_desatualizada"] is True and (e["latitude"], e["longitude"]) == (-8.0, -34.8)


def test_modo_novos_nao_atualiza_existentes_e_modo_atualizar_nao_cria(login, owner_engine):
    c, h = login("adm", "admin")
    eid = _base_existente(c, h, owner_engine)
    rows = [L(nome="MUDOU"), L(nome="NOVA EMPRESA", cnpj=fmt(C2))]
    r1 = confirmar(c, h, previa(c, h, rows), "novos").json()["resultado"]
    assert r1["criadas"] == 1 and r1["atualizadas"] == 0 and r1["ignoradas_pelo_modo"] == 1
    assert c.get(f"/api/empresas/{eid}").json()["nome"] == "NOME ANTIGO"
    rows2 = [L(nome="MUDOU"), L(nome="OUTRA NOVA", cnpj=fmt(C3))]
    r2 = confirmar(c, h, previa(c, h, rows2), "atualizar").json()["resultado"]
    assert r2["atualizadas"] == 1 and r2["criadas"] == 0 and len(empresas(owner_engine)) == 2


def test_modo_ambos(login, owner_engine):
    c, h = login("adm", "admin")
    _base_existente(c, h, owner_engine)
    r = confirmar(c, h, previa(c, h, [L(nome="MUDOU"), L(nome="NOVA", cnpj=fmt(C2))]), "ambos").json()["resultado"]
    assert r["criadas"] == 1 and r["atualizadas"] == 1


def test_empresa_criada_entre_previa_e_confirmacao_nao_duplica(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome="DA PLANILHA")])                      # na prévia o CNPJ ainda não existe
    c.post("/api/empresas", headers=h, json={"nome": "CRIADA A MAO", "cnpj": C1})
    r = confirmar(c, h, imp, "novos").json()["resultado"]            # o banco mudou: a execução reavalia
    assert r["criadas"] == 0 and r["ignoradas_pelo_modo"] == 1 and len(empresas(owner_engine)) == 1
    imp2 = previa(c, h, [L(nome="DA PLANILHA 2")])
    r2 = confirmar(c, h, imp2, "ambos").json()["resultado"]
    assert r2["atualizadas"] == 1 and len(empresas(owner_engine)) == 1


def test_cnpj_de_empresa_excluida_e_rejeitado(login):
    c, h = login("adm", "admin")
    eid = c.post("/api/empresas", headers=h, json={"nome": "X", "cnpj": C1}).json()["id"]
    c.delete(f"/api/empresas/{eid}", headers=h)
    imp = previa(c, h, [L()])
    assert imp["resumo"]["rejeitadas"] == 1 and imp["resumo"]["novas"] == 0


# ───────── Cabeçalhos e estrutura ─────────
def test_cabecalho_ausente_422_com_a_lista(login):
    c, h = login("adm", "admin")
    sem = [x for x in HEAD if x not in ("CNPJ", "CEP")]
    r = enviar(c, h, xlsx([[1] * len(sem)], headers=sem))
    assert r.status_code == 422 and "CNPJ" in r.json()["detail"] and "CEP" in r.json()["detail"]


def test_cabecalho_sem_acento_e_maiusculas_e_coluna_extra_ignorada(login, owner_engine):
    c, h = login("adm", "admin")
    cab = [x.upper().replace("Ú", "U") for x in HEAD] + ["Observação interna"]
    imp = previa(c, h, [L() + ["segredo"]], headers=cab)
    assert imp["resumo"]["cabecalhos_ignorados"] == ["Observação interna"]
    confirmar(c, h, imp)
    assert len(empresas(owner_engine)) == 1


def test_ordem_das_colunas_nao_importa(login, owner_engine):
    c, h = login("adm", "admin")
    ordem = list(reversed(range(14)))
    cab = [HEAD[i] for i in ordem]
    linha = L()
    confirmar(c, h, previa(c, h, [[linha[i] for i in ordem]], headers=cab))
    assert empresas(owner_engine)[0]["cnpj"] == C1


def test_cabecalho_repetido_planilha_vazia_e_sem_dados(login):
    c, h = login("adm", "admin")
    assert enviar(c, h, xlsx([L()], headers=HEAD[:-1] + ["CNPJ"])).status_code == 422
    assert enviar(c, h, xlsx([])).status_code == 422
    wb = Workbook()
    b = io.BytesIO(); wb.save(b)
    assert enviar(c, h, b.getvalue()).status_code == 422


def test_linhas_em_branco_sao_ignoradas(login):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(), [""] * 14, [None] * 14, L(nome="B", cnpj=fmt(C2))])
    assert imp["resumo"]["linhas_lidas"] == 2 and imp["resumo"]["linhas_em_branco"] == 2


def test_linha_sem_nome_ou_com_campo_gigante_e_rejeitada_e_o_resto_segue(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome=""), L(nome="X" * 301, cnpj=fmt(C2)), L(nome="BOA", cnpj=fmt(C3))])
    assert imp["resumo"]["rejeitadas"] == 2 and imp["resumo"]["novas"] == 1
    erros = c.get(f"/api/importacoes/{imp['id']}/linhas?acao=rejeitada").json()["itens"]
    assert {e["linha"] for e in erros} == {2, 3} and all(e["erros"] for e in erros)
    confirmar(c, h, imp)
    assert [e["nome"] for e in empresas(owner_engine)] == ["BOA"]


def test_varias_abas_le_a_primeira_e_avisa(login):
    wb = Workbook(); ws = wb.active; ws.append(HEAD); ws.append(L()); wb.create_sheet("Outra").append(["lixo"])
    b = io.BytesIO(); wb.save(b)
    c, h = login("adm", "admin")
    imp = enviar(c, h, b.getvalue()).json()
    assert imp["resumo"]["novas"] == 1 and "2 abas" in imp["resumo"]["avisos"][0]


# ───────── Injeção de fórmula / XSS ─────────
def test_formulas_sao_neutralizadas_e_nunca_executadas(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome="=HYPERLINK(\"http://evil\",\"x\")", complemento="@SUM(1+1)", bairro="+cmd|' /C calc'!A0", rua="-2+3")])
    assert any("neutralizado" in a for a in c.get(f"/api/importacoes/{imp['id']}/linhas").json()["itens"][0]["avisos"])
    confirmar(c, h, imp)
    e = empresas(owner_engine)[0]
    for campo in ("nome", "complemento", "bairro", "rua"):
        assert e[campo][0] == "'" and e[campo][1] in "=@+-"


def test_xss_na_planilha_fica_como_texto(login):
    c, h = login("adm", "admin")
    xss = "<script>alert(1)</script>"
    confirmar(c, h, previa(c, h, [L(nome=xss, rua=xss)]))
    e = c.get("/api/empresas").json()["itens"][0]
    assert e["nome"] == xss and e["rua"] == xss


# ───────── Segurança do upload ─────────
def test_extensao_e_mime_falsos(login):
    c, h = login("adm", "admin")
    ok = xlsx([L()])
    assert enviar(c, h, ok, nome="clientes.xls").status_code == 201   # extensão .xls é aceita; o tipo real vem do CONTEÚDO
    assert enviar(c, h, ok, nome="clientes.csv").status_code == 422
    assert enviar(c, h, ok, nome="clientes.xlsm").status_code == 422
    assert enviar(c, h, ok, nome="clientes").status_code == 422
    assert enviar(c, h, ok, mime="image/png").status_code == 422
    assert enviar(c, h, ok, mime="text/html").status_code == 422
    assert enviar(c, h, ok, nome="clientes.xlsx", mime="application/octet-stream").status_code == 201


def test_conteudo_falso_corrompido_e_vazio(login):
    c, h = login("adm", "admin")
    assert enviar(c, h, b"isto e so texto <script>alert(1)</script>").status_code == 422
    assert enviar(c, h, b"PK\x03\x04" + b"lixo" * 100).status_code == 422
    assert enviar(c, h, b"%PDF-1.4 fake").status_code == 422
    assert enviar(c, h, xlsx([L()])[:200]).status_code == 422          # truncado
    assert enviar(c, h, b"").status_code == 422
    r = enviar(c, h, b"PK\x03\x04" + b"lixo" * 100)
    assert "Traceback" not in r.text and "zipfile" not in r.text.lower()


def test_arquivo_grande_demais_com_content_length_e_em_partes(login, monkeypatch):
    c, h = login("adm", "admin")
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100_000)
    assert enviar(c, h, b"PK\x03\x04" + b"0" * 300_000).status_code == 413

    def partes():
        yield b'--x\r\nContent-Disposition: form-data; name="arquivo"; filename="a.xlsx"\r\nContent-Type: application/octet-stream\r\n\r\n'
        for _ in range(40):
            yield b"0" * 10_000
        yield b"\r\n--x--\r\n"
    r = c.post("/api/importacoes", content=partes(), headers={**h, "Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413


def test_zip_bomb_macros_caminhos_externos_e_senha(login):
    c, h = login("adm", "admin")
    bomba = zip_com({"xl/worksheets/bomba.xml": b"0" * 6_000_000})
    assert len(bomba) < 100_000 and enviar(c, h, bomba).status_code == 422
    assert enviar(c, h, zip_com({"xl/vbaProject.bin": b"x"})).status_code == 422
    assert enviar(c, h, zip_com({"xl/externalLinks/externalLink1.xml": b"<x/>"})).status_code == 422
    assert enviar(c, h, zip_com({"xl/embeddings/o.bin": b"x"})).status_code == 422
    assert enviar(c, h, zip_com({"../../etc/evil.txt": b"x"})).status_code == 422
    assert enviar(c, h, zip_com({"/abs/evil.txt": b"x"})).status_code == 422
    cripto = bytearray(zip_com({"xl/extra.xml": b"x"}))
    cripto[cripto.rfind(b"PK\x01\x02") + 8] |= 0x1          # marca a última entrada como criptografada
    assert enviar(c, h, bytes(cripto)).status_code == 422
    base = zipfile.ZipFile(io.BytesIO(xlsx([L()])))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for i in base.infolist():
            d = base.read(i.filename)
            if i.filename == "[Content_Types].xml":
                d = d.replace(b"</Types>", b'<Override PartName="/x" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/></Types>')
            z.writestr(i.filename, d)
    assert enviar(c, h, out.getvalue()).status_code == 422


def test_bomba_de_entidades_xml_e_bloqueada(login):
    c, h = login("adm", "admin")
    base = zipfile.ZipFile(io.BytesIO(xlsx([L()])))
    out = io.BytesIO()
    lol = '<!DOCTYPE lolz [<!ENTITY lol "lol">' + "".join(
        f'<!ENTITY lol{n} "{"&lol%s;" % (n - 1 if n > 1 else "") * 10}">'.replace("&lol;", "&lol;") for n in range(1, 8)) + "]>"
    with zipfile.ZipFile(out, "w") as z:
        for i in base.infolist():
            d = base.read(i.filename)
            if i.filename == "xl/workbook.xml":
                d = d.replace(b"<workbook", lol.encode() + b"<workbook", 1) if b"<?xml" not in d else d.replace(b"?>", b"?>" + lol.encode(), 1)
            z.writestr(i.filename, d)
    r = enviar(c, h, out.getvalue())
    assert r.status_code == 422 and "Traceback" not in r.text


def test_celula_com_formula_real_nao_e_calculada(login, owner_engine):
    wb = Workbook(); ws = wb.active; ws.append(HEAD); ws.append(L(complemento="x"))
    ws.cell(row=2, column=HEAD.index("Complemento") + 1).value = "=1+1"      # fórmula de verdade (sem valor guardado)
    b = io.BytesIO(); wb.save(b)
    c, h = login("adm", "admin")
    imp = enviar(c, h, b.getvalue()).json()
    confirmar(c, h, imp)
    assert empresas(owner_engine)[0]["complemento"] is None                    # nunca avaliada nem gravada como texto


def test_limite_de_linhas(login, monkeypatch):
    from app.modules.importacao import parser
    monkeypatch.setattr(parser, "MAX_LINHAS", 5)
    c, h = login("adm", "admin")
    assert enviar(c, h, xlsx([L(nome=f"E{i}", cnpj="") for i in range(6)])).status_code == 422
    assert enviar(c, h, xlsx([L(nome=f"E{i}", cnpj="") for i in range(5)])).status_code == 201


def test_nome_do_arquivo_malicioso_so_serve_para_exibicao(login, owner_engine):
    c, h = login("adm", "admin")
    r = enviar(c, h, xlsx([L()]), nome="../../etc/<script>x</script>.xlsx")
    assert r.status_code == 201 and "/" not in r.json()["nome_arquivo"] and "\\" not in r.json()["nome_arquivo"]


def test_limite_de_envios_por_hora(login):
    c, h = login("adm", "admin")
    ok = xlsx([L()])
    codes = [enviar(c, h, ok).status_code for _ in range(12)]
    assert codes[:10] == [201] * 10 and 429 in codes[10:]


# ───────── Permissões ─────────
@pytest.mark.parametrize("papel", ["operador", "visualizador"])
def test_so_administrador_importa(login, papel):
    c, h = login("user", papel)
    assert enviar(c, h, xlsx([L()])).status_code == 403
    assert c.get("/api/importacoes").status_code == 403
    assert c.get("/api/importacoes/1").status_code == 403
    assert c.get("/api/importacoes/1/linhas").status_code == 403
    assert c.post("/api/importacoes/1/confirmar", json={"modo": "novos"}, headers=h).status_code == 403
    assert c.post("/api/importacoes/1/cancelar", headers=h).status_code == 403


def test_sem_login_e_sem_csrf(client, login):
    assert client.post("/api/importacoes", files={"arquivo": ("a.xlsx", xlsx([L()]), MIME)}, headers=ORIGIN).status_code == 401
    c, h = login("adm", "admin")
    assert c.post("/api/importacoes", files={"arquivo": ("a.xlsx", xlsx([L()]), MIME)}, headers=ORIGIN).status_code == 403
    imp = previa(c, h, [L()])
    assert c.post(f"/api/importacoes/{imp['id']}/confirmar", json={"modo": "novos"}, headers=ORIGIN).status_code == 403
    assert c.post(f"/api/importacoes/{imp['id']}/cancelar", headers=ORIGIN).status_code == 403


def test_ids_modos_e_parametros_invalidos(login):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L()])
    assert c.get("/api/importacoes/99999").status_code == 404
    assert c.post("/api/importacoes/99999/confirmar", json={"modo": "novos"}, headers=h).status_code == 404
    assert c.post("/api/importacoes/abc/confirmar", json={"modo": "novos"}, headers=h).status_code == 422
    for corpo in [{"modo": "tudo"}, {}, {"modo": "novos", "extra": 1}]:
        assert c.post(f"/api/importacoes/{imp['id']}/confirmar", json=corpo, headers=h).status_code == 422
    for q in ["acao=hack", "page_size=500", "page=0"]:
        assert c.get(f"/api/importacoes/{imp['id']}/linhas?{q}").status_code == 422


# ───────── Confirmação: uso único, concorrência, atomicidade, LGPD ─────────
def test_confirmar_duas_vezes_e_cancelar_e_prévia_encerrada(login, owner_engine):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L()])
    assert confirmar(c, h, imp).status_code == 200
    assert confirmar(c, h, imp).status_code == 409
    assert c.post(f"/api/importacoes/{imp['id']}/cancelar", headers=h).status_code == 409
    assert c.get(f"/api/importacoes/{imp['id']}/linhas").status_code == 409
    assert len(empresas(owner_engine)) == 1
    imp2 = previa(c, h, [L(nome="B", cnpj=fmt(C2))])
    assert c.post(f"/api/importacoes/{imp2['id']}/cancelar", headers=h).json()["status"] == "cancelada"
    assert confirmar(c, h, imp2).status_code == 409
    assert len(empresas(owner_engine)) == 1


def test_confirmacoes_simultaneas_so_uma_vence(login, make_user, owner_engine):
    from fastapi.testclient import TestClient
    from app.main import app
    c1, h1 = login("adm1", "admin")
    make_user("adm2", "admin")
    c2 = TestClient(app, base_url="http://localhost:5173")
    r2 = c2.post("/api/auth/login", json={"username": "adm2", "password": PASSWORD}, headers=ORIGIN)
    h2 = {**ORIGIN, "X-CSRF-Token": r2.json()["csrf_token"]}
    imp = previa(c1, h1, [L(nome=f"E{i}", cnpj=fmt(cnpj_ok(f"1000000{i:05d}"))) for i in range(30)])
    res = []
    ts = [threading.Thread(target=lambda c=c, h=h: res.append(confirmar(c, h, imp, "novos").status_code)) for c, h in ((c1, h1), (c2, h2)) * 2]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(res) == [200, 409, 409, 409] and len(empresas(owner_engine)) == 30
    c2.close()


def test_falha_no_meio_nao_aplica_nada(login, owner_engine):
    c, h = login("adm", "admin")
    with owner_engine.begin() as db:
        db.execute(text("CREATE TRIGGER boom BEFORE INSERT ON empresas WHEN NEW.nome = 'BOOM' BEGIN SELECT RAISE(ABORT, 'falha simulada'); END"))
    imp = previa(c, h, [L(nome="UM", cnpj=fmt(C1)), L(nome="DOIS", cnpj=fmt(C2)), L(nome="BOOM", cnpj=fmt(C3))])
    r = confirmar(c, h, imp)
    assert r.status_code == 500 and "falha simulada" not in r.text and "nenhuma empresa foi alterada" in r.json()["detail"]
    assert empresas(owner_engine) == []                                     # atomicidade: nada ficou pela metade
    assert c.get(f"/api/importacoes/{imp['id']}").json()["status"] == "falhou"
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM audit_log WHERE action='importacao_falhou'")).scalar_one() == 1


def test_planilha_nao_fica_guardada_depois_da_confirmacao(login, owner_engine):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L()]))
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM importacao_linhas")).scalar_one() == 0
    imp = previa(c, h, [L(nome="B", cnpj=fmt(C2))])
    c.post(f"/api/importacoes/{imp['id']}/cancelar", headers=h)
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM importacao_linhas")).scalar_one() == 0


def test_previas_antigas_sao_descartadas_no_proximo_envio(login, owner_engine):
    c, h = login("adm", "admin")
    velha = previa(c, h, [L()])
    with owner_engine.begin() as db:
        db.execute(text("UPDATE importacoes SET criada_em = '2000-01-01 00:00:00.000'"))
    previa(c, h, [L(nome="B", cnpj=fmt(C2))])
    assert c.get(f"/api/importacoes/{velha['id']}").json()["status"] == "cancelada"
    with owner_engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM importacao_linhas WHERE importacao_id=:i"), {"i": velha["id"]}).scalar_one() == 0


def test_auditoria_nao_guarda_dados_das_empresas_nem_do_arquivo(login, owner_engine):
    c, h = login("adm", "admin")
    confirmar(c, h, previa(c, h, [L(nome="SEGREDO COMERCIAL LTDA", email="sigilo@empresa.com", celular="81999990000")]), "ambos")
    with owner_engine.begin() as db:
        dump = " ".join(str(tuple(r)) for r in db.execute(text("SELECT * FROM audit_log")).all())
        acoes = {r[0] for r in db.execute(text("SELECT action FROM audit_log"))}
    assert {"importacao_enviada", "importacao_confirmada"} <= acoes
    for pii in ["SEGREDO", "sigilo@", "81999990000", C1, "clientes.xlsx"]:
        assert pii not in dump


def test_historico_de_importacoes_nao_expoe_hash_nem_dados(login):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L()])
    confirmar(c, h, imp)
    hist = c.get("/api/importacoes").json()
    assert hist[0]["status"] == "concluida" and hist[0]["usuario"] == "adm" and hist[0]["resultado"]["criadas"] == 1
    assert "sha256" not in hist[0] and "dados" not in str(hist[0])


def test_linhas_da_previa_filtros_e_paginacao(login):
    c, h = login("adm", "admin")
    imp = previa(c, h, [L(nome=f"E{i}", cnpj=fmt(cnpj_ok(f"2000000{i:05d}"))) for i in range(7)] + [L(nome="SEM", cnpj="")])
    r = c.get(f"/api/importacoes/{imp['id']}/linhas?page_size=3&page=3").json()
    assert r["total"] == 8 and len(r["itens"]) == 2
    assert c.get(f"/api/importacoes/{imp['id']}/linhas?somente_revisao=true").json()["total"] == 1
    assert c.get(f"/api/importacoes/{imp['id']}/linhas?acao=novo").json()["total"] == 8
    assert c.get(f"/api/importacoes/{imp['id']}/linhas?acao=rejeitada").json()["total"] == 0


# ───────── Planilha no formato exportado pelo Asaas (.xls) ─────────
ASAAS_HEAD = ["Identificador externo", "Nome", "Email", "Emails adicionais", "Celular", "Empresa", "CPF ou CNPJ", "Fone", "Rua",
              "Número", "Complemento", "Bairro", "Cidade", "CEP", "Estado", "Valor vencido", "Valor pago", "Valor a vencer"]


def asaas_linha(**kw):
    base = {"Nome": "HAPPY KIDS BRINQUEDOS LTDA", "Email": "loja@example.com", "Celular": "87991384045", "Empresa": "HAPPY KIDS",
            "CPF ou CNPJ": C1, "Rua": "R GENERAL DANTAS BARRETO", "Número": "38", "Bairro": "SANTO ANTONIO",
            "Cidade": "Garanhuns - Pernambuco", "CEP": "55293465", "Estado": "PE", "Valor vencido": "R$ 0,00",
            "Valor pago": "R$ 700,00", "Valor a vencer": "R$ 0,00"}
    base.update(kw)
    return [base.get(k, "") for k in ASAAS_HEAD]


def xls(rows, headers=ASAAS_HEAD) -> bytes:
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet0")
    for j, h in enumerate(headers):
        ws.write(0, j, h)
    for i, r in enumerate(rows, start=1):
        for j, v in enumerate(r):
            ws.write(i, j, v)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_xls_do_asaas_mapeia_colunas_e_ignora_as_demais(login, owner_engine):
    c, h = login("adm", "admin")
    dados = xls([asaas_linha(), asaas_linha(**{"Nome": "OUTRA LTDA", "CPF ou CNPJ": C2, "Empresa": "", "CEP": "5293465"})])
    r = enviar(c, h, dados, nome="Clientes.xls", mime="application/vnd.ms-excel")
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["resumo"]["novas"] == 2 and imp["resumo"]["linhas_lidas"] == 2
    assert set(imp["resumo"]["cabecalhos_ignorados"]) == {"Identificador externo", "Valor vencido", "Valor pago", "Valor a vencer"}
    assert imp["resumo"]["novas_para_revisao"] == 1   # CEP com 7 dígitos
    assert confirmar(c, h, imp).status_code == 200
    e1, e2 = empresas(owner_engine)
    assert (e1["nome"], e1["nome_fantasia"], e1["cnpj"]) == ("HAPPY KIDS BRINQUEDOS LTDA", "HAPPY KIDS", C1)
    assert (e1["cidade"], e1["estado"], e1["cep"], e1["celular"], e1["numero"]) == ("Garanhuns", "PE", "55293465", "87991384045", "38")
    assert e2["nome_fantasia"] is None and e2["cnpj"] == C2


def test_xlsx_com_cabecalhos_do_asaas_e_nomes_antigos_continuam_valendo(login):
    c, h = login("adm", "admin")
    assert enviar(c, h, xlsx([asaas_linha()], headers=ASAAS_HEAD)).status_code == 201
    assert enviar(c, h, xlsx([L()])).status_code == 201   # cabeçalhos antigos (Nome da Empresa, Nome Fantasia, CNPJ)


def test_cabecalho_novo_e_antigo_juntos_sao_repetidos(login):
    c, h = login("adm", "admin")
    r = enviar(c, h, xlsx([asaas_linha() + ["x"]], headers=ASAAS_HEAD + ["Nome da Empresa"]))
    assert r.status_code == 422 and "repetido" in r.json()["detail"].lower()


def test_xls_falta_coluna_obrigatoria(login):
    c, h = login("adm", "admin")
    sem = [x for x in ASAAS_HEAD if x != "CPF ou CNPJ"]
    r = enviar(c, h, xls([[""] * len(sem)], headers=sem), nome="a.xls", mime="application/vnd.ms-excel")
    assert r.status_code == 422 and "CPF ou CNPJ" in r.json()["detail"]


def test_xls_corrompido_ou_com_assinatura_falsa_e_recusado(login):
    c, h = login("adm", "admin")
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    for lixo in (ole + b"\x00" * 600, ole, b"MZ" + b"\x00" * 600, b"<html>x</html>", xls([asaas_linha()])[:300]):
        r = enviar(c, h, lixo, nome="a.xls", mime="application/vnd.ms-excel")
        assert r.status_code == 422, (lixo[:10], r.text)
        assert "Traceback" not in r.text
