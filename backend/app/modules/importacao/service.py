import json

from sqlalchemy import Connection, text

from app import timeutil
from app.modules.importacao import rules
from app.modules.importacao.parser import Planilha

ATUALIZAVEIS = ("nome", "nome_fantasia", "email", "celular", "fone", "cep", "rua", "numero", "complemento", "bairro", "cidade", "estado")
ENDERECO = ("rua", "numero", "complemento", "bairro", "cidade", "cep", "estado")
BUSCA_POR_CNPJ = ("SELECT id, deleted_at, nome, nome_fantasia, email, emails_adicionais, celular, fone, cep, rua, numero, "
                  "complemento, bairro, cidade, estado, latitude, geocode_desatualizada FROM empresas WHERE cnpj = :c")

INSERT_EMPRESA = (
    "INSERT INTO empresas (nome, nome_fantasia, cnpj, cnpj_original, email, emails_adicionais, celular, fone, cep, rua, numero, "
    "numero_base, complemento, bairro, cidade, estado, precisa_revisao, motivos_revisao, importacao_id, created_by, updated_by) "
    "VALUES (:nome, :nome_fantasia, :cnpj, :cnpj_original, :email, :emails_adicionais, :celular, :fone, :cep, :rua, :numero, "
    ":numero_base, :complemento, :bairro, :cidade, :estado, :rev, :motivos, :imp, :uid, :uid)"
)
UPDATE_EMPRESA = (
    "UPDATE empresas SET nome=:nome, nome_fantasia=:nome_fantasia, email=:email, emails_adicionais=:emails_adicionais, "
    "celular=:celular, fone=:fone, cep=:cep, rua=:rua, numero=:numero, numero_base=:numero_base, complemento=:complemento, "
    "bairro=:bairro, cidade=:cidade, estado=:estado, geocode_desatualizada=:desat, "
    "geocode_status = CASE WHEN :end = 1 THEN 'pendente' ELSE geocode_status END, "
    "version = version + 1, updated_by = :uid WHERE id = :id AND deleted_at IS NULL"
)


def calcular_mudancas(atual: dict, dados: dict) -> tuple[dict, list[str]]:
    """A planilha só ACRESCENTA ou CORRIGE: valor vazio nunca apaga dado existente; o que não está na
    planilha (situação, porte, categorias, campos personalizados, coordenadas) não é tocado."""
    novo = {k: atual[k] for k in ATUALIZAVEIS}
    alterados = []
    for k in ATUALIZAVEIS:
        v = dados.get(k)
        if v not in (None, "") and v != atual[k]:
            novo[k] = v
            alterados.append(k)
    antigos = json.loads(atual["emails_adicionais"] or "[]")
    candidatos = antigos + list(dados.get("emails_adicionais") or [])
    if "email" in alterados and atual["email"]:
        candidatos.append(atual["email"])  # o email antigo não se perde: vai para os adicionais
    uniao = [e for e in dict.fromkeys(candidatos) if e != novo["email"]][:20]
    if uniao != antigos:
        alterados.append("emails_adicionais")
    novo["emails_adicionais"] = uniao
    return novo, alterados


def _chaves_sem_cnpj(db: Connection) -> dict[str, int]:
    rows = db.execute(text("SELECT id, nome, rua, numero, cidade FROM empresas WHERE cnpj IS NULL AND deleted_at IS NULL")).mappings().all()
    return {rules.chave_sem_cnpj(dict(r)): r["id"] for r in rows}


def classificar(db: Connection, plan: Planilha) -> list[dict]:
    itens = []
    for n, raw in plan.linhas:
        r = rules.processar(raw)
        itens.append({"linha": n, "dados": r.dados, "motivos": r.motivos, "avisos": r.avisos, "erros": r.erros,
                      "acao": "rejeitada" if r.erros else None, "mantida": None, "empresa_id": None, "alteracoes": []})
    grupos: dict[tuple, list[dict]] = {}
    for it in itens:
        if it["acao"]:
            continue
        d = it["dados"]
        grupos.setdefault(("c", d["cnpj"]) if d["cnpj"] else ("s", rules.chave_sem_cnpj(d)), []).append(it)
    for membros in grupos.values():
        if len(membros) > 1:  # regra escolhida: manter o registro MAIS COMPLETO (empate: o que aparece primeiro)
            venc = max(membros, key=lambda i: (*rules.completude(i["dados"]), -i["linha"]))
            for i in membros:
                if i is not venc:
                    i["acao"], i["mantida"] = "duplicada", venc["linha"]
    sem_cnpj = _chaves_sem_cnpj(db)
    for it in itens:
        if it["acao"]:
            continue
        d = it["dados"]
        if d["cnpj"]:
            atual = db.execute(text(BUSCA_POR_CNPJ), {"c": d["cnpj"]}).mappings().first()
            if not atual:
                it["acao"] = "novo"
            elif atual["deleted_at"]:
                it["acao"], it["erros"] = "rejeitada", ["Este CNPJ pertence a uma empresa excluída."]
            else:
                _, alt = calcular_mudancas(dict(atual), d)
                it["acao"], it["empresa_id"], it["alteracoes"] = ("atualizar" if alt else "sem_alteracao"), atual["id"], alt
        else:
            ex = sem_cnpj.get(rules.chave_sem_cnpj(d))
            it["acao"], it["empresa_id"] = ("ja_existente", ex) if ex else ("novo", None)
    return itens


def resumir(plan: Planilha, itens: list[dict]) -> dict:
    cont = {a: 0 for a in ("novo", "atualizar", "sem_alteracao", "duplicada", "rejeitada", "ja_existente")}
    for i in itens:
        cont[i["acao"]] += 1
    return {"linhas_lidas": len(itens), "linhas_em_branco": plan.em_branco, "novas": cont["novo"],
            "novas_para_revisao": sum(1 for i in itens if i["acao"] == "novo" and i["motivos"]),
            "para_atualizar": cont["atualizar"], "sem_alteracao": cont["sem_alteracao"],
            "ja_existentes_sem_cnpj": cont["ja_existente"], "duplicadas_descartadas": cont["duplicada"],
            "rejeitadas": cont["rejeitada"], "cabecalhos_ignorados": plan.cabecalhos_ignorados, "avisos": plan.avisos}


def persistir(db: Connection, usuario_id: int, nome: str, sha: str, tamanho: int, plan: Planilha, itens: list[dict]) -> tuple[int, dict]:
    resumo = resumir(plan, itens)
    iid = db.execute(text("INSERT INTO importacoes (usuario_id, nome_arquivo, sha256, tamanho, status, resumo, criada_em) "
                          "VALUES (:u, :n, :s, :t, 'previa', :r, :now) RETURNING id"),
                     {"u": usuario_id, "n": nome, "s": sha, "t": tamanho, "r": json.dumps(resumo), "now": timeutil.now()}).scalar_one()
    db.execute(text("INSERT INTO importacao_linhas (importacao_id, linha, acao, revisao, dados, motivos, avisos, erros, alteracoes, mantida_linha, empresa_id) "
                    "VALUES (:i, :l, :a, :rev, :d, :m, :av, :e, :alt, :man, :emp)"),
               [{"i": iid, "l": x["linha"], "a": x["acao"], "rev": int(bool(x["motivos"])), "d": json.dumps(x["dados"]),
                 "m": json.dumps(x["motivos"]), "av": json.dumps(x["avisos"]), "e": json.dumps(x["erros"]),
                 "alt": json.dumps(x["alteracoes"]), "man": x["mantida"], "emp": x["empresa_id"]} for x in itens])
    return iid, resumo


def executar(db: Connection, imp_id: int, modo: str, uid: int) -> dict:
    """Roda dentro de UMA transação: ou aplica tudo, ou nada. Reavalia cada linha contra o banco AGORA
    (a prévia pode ter ficado velha) e nunca confia no que foi decidido na prévia."""
    r = {"criadas": 0, "criadas_para_revisao": 0, "atualizadas": 0, "sem_alteracao": 0, "ja_existentes": 0,
         "ignoradas_pelo_modo": 0, "rejeitadas": 0, "duplicadas_descartadas": 0}
    for a, c in db.execute(text("SELECT acao, count(*) FROM importacao_linhas WHERE importacao_id=:i AND acao IN ('rejeitada','duplicada') GROUP BY acao"), {"i": imp_id}):
        r["rejeitadas" if a == "rejeitada" else "duplicadas_descartadas"] += c
    sem_cnpj = _chaves_sem_cnpj(db)
    linhas = db.execute(text("SELECT dados, motivos FROM importacao_linhas WHERE importacao_id=:i AND acao IN ('novo','atualizar') ORDER BY linha"),
                        {"i": imp_id}).all()
    for dados_js, motivos_js in linhas:
        d, motivos = json.loads(dados_js), json.loads(motivos_js)
        atual = db.execute(text(BUSCA_POR_CNPJ), {"c": d["cnpj"]}).mappings().first() if d["cnpj"] else None
        if atual:
            if atual["deleted_at"]:
                r["rejeitadas"] += 1
            elif modo == "novos":
                r["ignoradas_pelo_modo"] += 1
            else:
                atual = dict(atual)
                novo, alt = calcular_mudancas(atual, d)
                if not alt:
                    r["sem_alteracao"] += 1
                    continue
                end = int(any(k in alt for k in ENDERECO))
                db.execute(text(UPDATE_EMPRESA), {**novo, "emails_adicionais": json.dumps(novo["emails_adicionais"]),
                           "numero_base": d["numero_base"] if "numero" in alt else _nb(atual["numero"]), "end": end,
                           "desat": int(bool(atual["geocode_desatualizada"]) or (end and atual["latitude"] is not None)),
                           "uid": uid, "id": atual["id"]})
                r["atualizadas"] += 1
            continue
        chave = rules.chave_sem_cnpj(d)
        if not d["cnpj"] and chave in sem_cnpj:
            r["ja_existentes"] += 1
        elif modo == "atualizar":
            r["ignoradas_pelo_modo"] += 1
        else:
            db.execute(text(INSERT_EMPRESA), {**d, "emails_adicionais": json.dumps(d["emails_adicionais"]), "rev": int(bool(motivos)),
                       "motivos": json.dumps(motivos), "imp": imp_id, "uid": uid})
            if not d["cnpj"]:
                sem_cnpj[chave] = -1
            r["criadas"] += 1
            r["criadas_para_revisao"] += int(bool(motivos))
    return r


def _nb(numero):
    from app.modules.companies.normalizers import numero_base
    return numero_base(numero)
