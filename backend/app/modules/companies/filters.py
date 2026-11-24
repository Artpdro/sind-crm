"""Filtros de empresas, compartilhados por lista, mapa e PDF: assim os três mostram EXATAMENTE os mesmos resultados.
Todo valor vai como parâmetro; os pedaços de SQL são fixos."""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from fastapi import HTTPException, Query
from sqlalchemy import Connection, text

from app.modules.companies import normalizers as nz
from app.textutil import sem_acento

ESC = " ESCAPE '\\'"
StatusGeo = Literal["localizado", "pendente", "nao_encontrado", "revisao"]


@dataclass
class Filtros:
    q: str | None = None
    rua: str | None = None
    bairro: str | None = None
    numero: str | None = None
    cidade: str | None = None
    paridade: str = "todos"
    situacao_id: int | None = None
    porte_id: int | None = None
    tipo_empresa_id: int | None = None
    categoria_id: int | None = None
    revisao: bool | None = None
    geocode_status: str | None = None
    campos: list[str] = field(default_factory=list)


def filtros_query(q: str | None = Query(None, max_length=100), rua: str | None = Query(None, max_length=300),
                  bairro: str | None = Query(None, max_length=150), numero: str | None = Query(None, max_length=60),
                  cidade: str | None = Query(None, max_length=150),
                  paridade: Literal["todos", "pares", "impares"] = Query("todos"),
                  situacao_id: int | None = Query(None, ge=1), porte_id: int | None = Query(None, ge=1),
                  tipo_empresa_id: int | None = Query(None, ge=1), categoria_id: int | None = Query(None, ge=1),
                  revisao: bool | None = Query(None), geocode_status: StatusGeo | None = Query(None),
                  campo: list[str] = Query(default_factory=list, max_length=10)) -> Filtros:
    return Filtros(q, rua, bairro, numero, cidade, paridade, situacao_id, porte_id, tipo_empresa_id, categoria_id,
                   revisao, geocode_status, campo)


def _contem(v: str) -> str:
    return "%" + (sem_acento(v) or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _cond_campo(tipo: str, valor: str, n: int, p: dict) -> str:
    chave = f"v{n}"
    if tipo == "booleano":
        v = sem_acento(valor)
        if v not in ("true", "false", "sim", "nao", "1", "0"):
            raise HTTPException(status_code=422, detail="Filtro de campo sim/não: use sim ou não.")
        p[chave] = int(v in ("true", "sim", "1"))
        return f"v.valor_bool = :{chave}"
    if tipo == "data":
        try:
            p[chave] = date.fromisoformat(valor).isoformat()
        except ValueError:
            raise HTTPException(status_code=422, detail="Filtro de campo data: use AAAA-MM-DD.")
        return f"v.valor_data = :{chave}"
    if tipo == "numero":
        try:
            p[chave] = float(valor)
        except ValueError:
            raise HTTPException(status_code=422, detail="Filtro de campo numérico inválido.")
        return f"v.valor_numero = :{chave}"
    if tipo == "selecao":
        p[chave] = valor
        return f"v.valor_texto = :{chave}"
    p[chave] = _contem(valor)
    return f"unaccent(v.valor_texto) LIKE :{chave}{ESC}"


def montar(db: Connection, f: Filtros, ignorar_paridade: bool = False) -> tuple[str, dict]:
    where, p = ["e.deleted_at IS NULL"], {}
    if f.q and f.q.strip():
        q = f.q.strip()
        digits = nz.digits_only(q)
        partes = [f"unaccent(e.nome) LIKE :q{ESC}", f"unaccent(e.nome_fantasia) LIKE :q{ESC}"]
        p["q"] = _contem(q)
        if len(digits) >= 3 and digits == q.replace(".", "").replace("/", "").replace("-", ""):
            partes.append("e.cnpj LIKE :qd")
            p["qd"] = digits + "%"
        where.append("(" + " OR ".join(partes) + ")")
    if f.rua and f.rua.strip():
        where.append(f"unaccent(e.rua) LIKE :rua{ESC}")  # rua: "contém" (cobre 'R.', 'Rua', 'Av.' e nomes completos)
        p["rua"] = _contem(f.rua)
    for col, val in (("bairro", f.bairro), ("cidade", f.cidade)):  # bairro e cidade: igualdade exata (sem acento/maiúsculas)
        if val and val.strip():
            where.append(f"unaccent(e.{col}) = :{col}")
            p[col] = sem_acento(val)
    if f.numero and f.numero.strip():
        n = f.numero.strip()
        if n.isdigit() and len(n) <= 9:  # "100" também acha 100A, 100-B e 100/102 (mesmo número-base)
            where.append("(unaccent(e.numero) = :numero OR e.numero_base = :nb)")
            p["nb"] = int(n)
        else:
            where.append("unaccent(e.numero) = :numero")
        p["numero"] = sem_acento(n)
    if f.paridade in ("pares", "impares") and not ignorar_paridade:
        # Sem número identificável (S/N, Fundos...) NÃO é classificado como par nem ímpar: fica de fora.
        where.append("e.numero_base IS NOT NULL AND e.numero_base % 2 = " + ("0" if f.paridade == "pares" else "1"))
    for col, val, nome in (("e.situacao_id", f.situacao_id, "s"), ("e.porte_id", f.porte_id, "p"), ("e.tipo_empresa_id", f.tipo_empresa_id, "t")):
        if val is not None:
            where.append(f"{col} = :{nome}")
            p[nome] = val
    if f.categoria_id is not None:
        where.append("EXISTS (SELECT 1 FROM empresa_categorias x WHERE x.empresa_id = e.id AND x.categoria_id = :cat)")
        p["cat"] = f.categoria_id
    if f.revisao:
        where.append("e.precisa_revisao = 1")
    if f.geocode_status:
        where.append("e.geocode_status = :gs")
        p["gs"] = f.geocode_status
    for n, item in enumerate(f.campos):
        m = re.fullmatch(r"([a-z][a-z0-9_]{1,49}):(.{1,200})", item, flags=re.S)
        if not m:
            raise HTTPException(status_code=422, detail="Filtro de campo personalizado inválido (use chave:valor).")
        d = db.execute(text("SELECT id, tipo FROM campos_personalizados WHERE chave = :c AND ativo = 1"), {"c": m.group(1)}).mappings().first()
        if not d:
            raise HTTPException(status_code=422, detail="Campo personalizado desconhecido no filtro.")
        cond = _cond_campo(d["tipo"], m.group(2).strip(), n, p)
        p[f"cf{n}"] = d["id"]
        # `cond` e `n` são fixos/numéricos: só compõem NOMES de parâmetros (:v0, :cf0); os valores vão sempre como parâmetros.
        where.append(f"EXISTS (SELECT 1 FROM empresa_campos_valores v WHERE v.empresa_id = e.id AND v.campo_id = :cf{n} AND {cond})")  # nosec B608
    return " AND ".join(where), p
