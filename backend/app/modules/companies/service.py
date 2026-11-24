import json
import math
from datetime import date

from fastapi import HTTPException
from sqlalchemy import Connection, text

EDITABLE = ("nome", "nome_fantasia", "cnpj", "email", "celular", "fone", "cep", "rua", "numero", "complemento",
            "bairro", "cidade", "estado", "porte_id", "tipo_empresa_id", "situacao_id", "informacoes_adicionais")
ADDRESS = ("rua", "numero", "complemento", "bairro", "cidade", "cep", "estado")

BASE_SELECT = (
    "SELECT e.*, s.nome AS situacao_nome, s.cor AS situacao_cor, p.nome AS porte_nome, t.nome AS tipo_nome "
    "FROM empresas e LEFT JOIN situacoes s ON s.id = e.situacao_id "
    "LEFT JOIN portes p ON p.id = e.porte_id LEFT JOIN tipos_empresa t ON t.id = e.tipo_empresa_id "
)


def fail(msg: str, code: int = 422):
    raise HTTPException(status_code=code, detail=msg)


def _base(row) -> dict:
    d = dict(row)
    d["emails_adicionais"] = json.loads(d["emails_adicionais"] or "[]")
    d["geocode_desatualizada"] = bool(d["geocode_desatualizada"])
    d["precisa_revisao"] = bool(d.get("precisa_revisao"))
    d["motivos_revisao"] = json.loads(d.get("motivos_revisao") or "[]")
    return d


def categorias_de(db: Connection, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    ph = ", ".join(f":i{n}" for n in range(len(ids)))  # só placeholders; valores vão como parâmetros
    rows = db.execute(text(
        "SELECT ec.empresa_id, c.id, c.nome FROM empresa_categorias ec JOIN categorias c ON c.id = ec.categoria_id "
        f"WHERE ec.empresa_id IN ({ph}) ORDER BY c.nome"),  # nosec B608
        {f"i{n}": v for n, v in enumerate(ids)}).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["empresa_id"], []).append({"id": r["id"], "nome": r["nome"]})
    return out


def campos_de(db: Connection, empresa_id: int) -> list[dict]:
    rows = db.execute(text(
        "SELECT c.chave, c.rotulo, c.tipo, v.valor_texto, v.valor_numero, v.valor_data, v.valor_bool "
        "FROM campos_personalizados c LEFT JOIN empresa_campos_valores v ON v.campo_id = c.id AND v.empresa_id = :e "
        "WHERE c.ativo = 1 ORDER BY c.ordem, c.id"), {"e": empresa_id}).mappings().all()
    out = []
    for r in rows:
        val = {"booleano": lambda: None if r["valor_bool"] is None else bool(r["valor_bool"]),
               "data": lambda: r["valor_data"], "numero": lambda: r["valor_numero"],
               "texto": lambda: r["valor_texto"], "selecao": lambda: r["valor_texto"]}[r["tipo"]]()
        out.append({"chave": r["chave"], "rotulo": r["rotulo"], "tipo": r["tipo"], "valor": val})
    return out


def carregar(db: Connection, empresa_id: int) -> dict | None:
    row = db.execute(text(BASE_SELECT + "WHERE e.id = :i AND e.deleted_at IS NULL"), {"i": empresa_id}).mappings().first()
    if not row:
        return None
    d = _base(row)
    d["categorias"] = categorias_de(db, [empresa_id]).get(empresa_id, [])
    d["campos"] = campos_de(db, empresa_id)
    return d


def checar_referencias(db: Connection, *, porte_id, tipo_empresa_id, situacao_id, categoria_ids) -> None:
    """Relacionamentos por ID: precisam existir e estar ativos."""
    for tabela_sql, valor, nome in (
        ("SELECT 1 FROM portes WHERE id = :i AND ativa = 1", porte_id, "Porte"),
        ("SELECT 1 FROM tipos_empresa WHERE id = :i AND ativa = 1", tipo_empresa_id, "Tipo de empresa"),
        ("SELECT 1 FROM situacoes WHERE id = :i AND ativa = 1", situacao_id, "Situação"),
    ):
        if valor is not None and not db.execute(text(tabela_sql), {"i": valor}).first():
            fail(f"{nome} inválido(a) ou inativo(a).")
    for cid in categoria_ids or []:
        if not db.execute(text("SELECT 1 FROM categorias WHERE id = :i AND ativa = 1"), {"i": cid}).first():
            fail("Categoria inválida ou inativa.")


def _converter(defn, valor):
    t, rot = defn["tipo"], defn["rotulo"]
    if t == "booleano":
        if not isinstance(valor, bool):
            fail(f"Campo '{rot}': informe verdadeiro ou falso.")
        return {"valor_bool": int(valor)}
    if t == "data":
        try:
            if not isinstance(valor, str) or len(valor) != 10:
                raise ValueError
            return {"valor_data": date.fromisoformat(valor).isoformat()}
        except ValueError:
            fail(f"Campo '{rot}': use a data no formato AAAA-MM-DD.")
    if t == "numero":
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
            fail(f"Campo '{rot}': informe um número.")
        return {"valor_numero": float(valor)}
    if t == "selecao":
        if valor not in json.loads(defn["opcoes"] or "[]"):
            fail(f"Campo '{rot}': opção inválida.")
        return {"valor_texto": valor}
    if not isinstance(valor, str) or len(valor) > 5000:  # texto
        fail(f"Campo '{rot}': texto inválido (máx. 5000 caracteres).")
    return {"valor_texto": valor}


def aplicar_campos(db: Connection, empresa_id: int, campos: dict | None, criando: bool) -> list[str]:
    defs = {r["chave"]: r for r in db.execute(text(
        "SELECT id, chave, rotulo, tipo, opcoes, obrigatorio FROM campos_personalizados WHERE ativo = 1")).mappings().all()}
    campos = campos or {}
    for chave in campos:
        if chave not in defs:
            fail(f"Campo personalizado desconhecido: {chave[:50]}")
    for chave, d in defs.items():
        if d["obrigatorio"] and ((criando and campos.get(chave) is None) or (chave in campos and campos[chave] is None)):
            fail(f"Campo '{d['rotulo']}' é obrigatório.")
    alterados = []
    for chave, valor in campos.items():
        d = defs[chave]
        if valor is None:
            db.execute(text("DELETE FROM empresa_campos_valores WHERE empresa_id = :e AND campo_id = :c"),
                       {"e": empresa_id, "c": d["id"]})
        else:
            cols = {"valor_texto": None, "valor_numero": None, "valor_data": None, "valor_bool": None, **_converter(d, valor)}
            db.execute(text(
                "INSERT INTO empresa_campos_valores (empresa_id, campo_id, valor_texto, valor_numero, valor_data, valor_bool) "
                "VALUES (:e, :c, :valor_texto, :valor_numero, :valor_data, :valor_bool) "
                "ON CONFLICT(empresa_id, campo_id) DO UPDATE SET valor_texto = excluded.valor_texto, "
                "valor_numero = excluded.valor_numero, valor_data = excluded.valor_data, valor_bool = excluded.valor_bool"),
                {"e": empresa_id, "c": d["id"], **cols})
        alterados.append(f"campo:{chave}")
    return alterados


def salvar_categorias(db: Connection, empresa_id: int, ids: list[int]) -> None:
    db.execute(text("DELETE FROM empresa_categorias WHERE empresa_id = :e"), {"e": empresa_id})
    for cid in ids:
        db.execute(text("INSERT INTO empresa_categorias (empresa_id, categoria_id) VALUES (:e, :c)"), {"e": empresa_id, "c": cid})
