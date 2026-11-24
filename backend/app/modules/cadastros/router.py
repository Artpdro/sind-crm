import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.modules.audit import service as audit
from app.modules.auth.schemas import Strict
from app.security import sessions

router = APIRouter(prefix="/api", tags=["cadastros"])
admin = sessions.require_role("admin")

# Nome público da rota -> tabela (lista fixa: nenhum nome de tabela vem do usuário)
TABELAS = {"situacoes": "situacoes", "categorias": "categorias", "portes": "portes", "tipos-empresa": "tipos_empresa"}


def _tabela(nome: str) -> str:
    if nome not in TABELAS:
        raise HTTPException(status_code=404, detail="Cadastro não encontrado.")
    return TABELAS[nome]


def _sql(template: str, tabela: str) -> str:
    return template.replace("TABELA", tabela)  # tabela vem da lista fixa acima


LISTA_SITUACOES = "SELECT id, nome, ativa, cor, ordem FROM TABELA WHERE (:so = 0 OR ativa = 1) ORDER BY ordem, nome"
LISTA_SIMPLES = "SELECT id, nome, ativa FROM TABELA WHERE (:so = 0 OR ativa = 1) ORDER BY nome"


def _nome(v: str) -> str:
    v = v.strip()
    if not 1 <= len(v) <= 80 or any(ord(c) < 32 for c in v):
        raise ValueError("Nome inválido (1 a 80 caracteres).")
    return v


class ItemIn(Strict):
    nome: str = Field(max_length=200)
    cor: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ordem: int | None = Field(default=None, ge=0, le=1000)
    _n = field_validator("nome")(lambda cls, v: _nome(v))


class ItemPatch(Strict):
    nome: str | None = Field(default=None, max_length=200)
    cor: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ordem: int | None = Field(default=None, ge=0, le=1000)
    ativa: bool | None = None

    @field_validator("nome")
    @classmethod
    def _n(cls, v):
        return _nome(v) if v is not None else v


def _row(r, tabela):
    d = {"id": r["id"], "nome": r["nome"], "ativa": bool(r["ativa"])}
    if tabela == "situacoes":
        d.update(cor=r["cor"], ordem=r["ordem"])
    return d


@router.get("/cadastros/{nome}")
def listar(nome: str, todas: bool = Query(False), user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    t = _tabela(nome)
    so_ativas = not (todas and user.role == "admin")  # inativas só aparecem para o administrador
    sql = _sql(LISTA_SITUACOES if t == "situacoes" else LISTA_SIMPLES, t)
    return [_row(r, t) for r in db.execute(text(sql), {"so": int(so_ativas)}).mappings().all()]


@router.post("/cadastros/{nome}", status_code=201)
def criar(nome: str, body: ItemIn, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    t = _tabela(nome)
    if t != "situacoes" and (body.cor is not None or body.ordem is not None):
        raise HTTPException(status_code=422, detail="Cor e ordem só existem para situações.")
    try:
        if t == "situacoes":
            iid = db.execute(text("INSERT INTO situacoes (nome, cor, ordem) VALUES (:n, COALESCE(:c, '#6b7280'), COALESCE(:o, 0)) RETURNING id"),
                             {"n": body.nome, "c": body.cor, "o": body.ordem}).scalar_one()
        else:
            iid = db.execute(text(_sql("INSERT INTO TABELA (nome) VALUES (:n) RETURNING id", t)), {"n": body.nome}).scalar_one()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe um item com este nome.")
    audit.record(db, request, "cadastro_criado", user_id=user.user_id, username=user.username,
                 entity_type=t, entity_id=iid, details={"nome": body.nome})
    db.commit()
    return _row(db.execute(text(_sql("SELECT * FROM TABELA WHERE id = :i", t)), {"i": iid}).mappings().one(), t)


@router.patch("/cadastros/{nome}/{item_id}")
def editar(nome: str, item_id: int, body: ItemPatch, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    t = _tabela(nome)
    ch = body.model_dump(exclude_unset=True)
    if not ch or (t != "situacoes" and ({"cor", "ordem"} & ch.keys())):
        raise HTTPException(status_code=422, detail="Nada válido para atualizar.")
    atual = db.execute(text(_sql("SELECT * FROM TABELA WHERE id = :i", t)), {"i": item_id}).mappings().first()
    if not atual:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    novo = {"n": ch.get("nome", atual["nome"]), "a": int(ch.get("ativa", bool(atual["ativa"]))), "i": item_id}
    try:
        if t == "situacoes":
            db.execute(text("UPDATE situacoes SET nome=:n, ativa=:a, cor=:c, ordem=:o WHERE id=:i"),
                       {**novo, "c": ch.get("cor", atual["cor"]), "o": ch.get("ordem", atual["ordem"])})
        else:
            db.execute(text(_sql("UPDATE TABELA SET nome=:n, ativa=:a WHERE id=:i", t)), novo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe um item com este nome.")
    audit.record(db, request, "cadastro_editado", user_id=user.user_id, username=user.username,
                 entity_type=t, entity_id=item_id, details={"campos": sorted(ch)})
    db.commit()
    return _row(db.execute(text(_sql("SELECT * FROM TABELA WHERE id = :i", t)), {"i": item_id}).mappings().one(), t)


@router.delete("/cadastros/{nome}/{item_id}", status_code=204)
def excluir(nome: str, item_id: int, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    t = _tabela(nome)
    try:
        n = db.execute(text(_sql("DELETE FROM TABELA WHERE id = :i", t)), {"i": item_id}).rowcount
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Este item está em uso. Desative-o em vez de excluir.")
    if not n:
        db.rollback()
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    audit.record(db, request, "cadastro_excluido", user_id=user.user_id, username=user.username, entity_type=t, entity_id=item_id)
    db.commit()


# ───────── Campos personalizados ─────────
class CampoIn(Strict):
    chave: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    rotulo: str = Field(min_length=1, max_length=120)
    tipo: Literal["booleano", "data", "texto", "numero", "selecao"]
    opcoes: list[str] | None = Field(default=None, max_length=50)
    obrigatorio: bool = False
    ordem: int = Field(default=0, ge=0, le=1000)


class CampoPatch(Strict):  # chave e tipo não mudam depois de criados
    rotulo: str | None = Field(default=None, min_length=1, max_length=120)
    opcoes: list[str] | None = Field(default=None, max_length=50)
    obrigatorio: bool | None = None
    ativo: bool | None = None
    ordem: int | None = Field(default=None, ge=0, le=1000)


def _opcoes_ok(opcoes):
    if opcoes is not None and (not opcoes or len(set(opcoes)) != len(opcoes) or any(not o.strip() or len(o) > 80 for o in opcoes)):
        raise HTTPException(status_code=422, detail="Opções inválidas (não vazias, sem repetição, até 80 caracteres).")


def _campo(r):
    return {"id": r["id"], "chave": r["chave"], "rotulo": r["rotulo"], "tipo": r["tipo"],
            "opcoes": json.loads(r["opcoes"]) if r["opcoes"] else None, "obrigatorio": bool(r["obrigatorio"]),
            "ativo": bool(r["ativo"]), "ordem": r["ordem"]}


@router.get("/campos")
def listar_campos(todos: bool = Query(False), user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    so_ativos = not (todos and user.role == "admin")
    rows = db.execute(text("SELECT * FROM campos_personalizados WHERE (:so = 0 OR ativo = 1) ORDER BY ordem, id"),
                      {"so": int(so_ativos)}).mappings().all()
    return [_campo(r) for r in rows]


@router.post("/campos", status_code=201)
def criar_campo(body: CampoIn, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    if body.tipo == "selecao":
        if body.opcoes is None:
            raise HTTPException(status_code=422, detail="Campos de seleção precisam de opções.")
        _opcoes_ok(body.opcoes)
    elif body.opcoes is not None:
        raise HTTPException(status_code=422, detail="Só campos de seleção têm opções.")
    try:
        cid = db.execute(text("INSERT INTO campos_personalizados (chave, rotulo, tipo, opcoes, obrigatorio, ordem) "
                              "VALUES (:c, :r, :t, :o, :ob, :ord) RETURNING id"),
                         {"c": body.chave, "r": body.rotulo, "t": body.tipo, "o": json.dumps(body.opcoes) if body.opcoes else None,
                          "ob": int(body.obrigatorio), "ord": body.ordem}).scalar_one()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe um campo com esta chave.")
    audit.record(db, request, "campo_criado", user_id=user.user_id, username=user.username,
                 entity_type="campo_personalizado", entity_id=cid, details={"chave": body.chave, "tipo": body.tipo})
    db.commit()
    return _campo(db.execute(text("SELECT * FROM campos_personalizados WHERE id = :i"), {"i": cid}).mappings().one())


@router.patch("/campos/{campo_id}")
def editar_campo(campo_id: int, body: CampoPatch, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    ch = body.model_dump(exclude_unset=True)
    if not ch:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    atual = db.execute(text("SELECT * FROM campos_personalizados WHERE id = :i"), {"i": campo_id}).mappings().first()
    if not atual:
        raise HTTPException(status_code=404, detail="Campo não encontrado.")
    if "opcoes" in ch:
        if atual["tipo"] != "selecao":
            raise HTTPException(status_code=422, detail="Só campos de seleção têm opções.")
        _opcoes_ok(ch["opcoes"] if ch["opcoes"] is not None else [])
    db.execute(text("UPDATE campos_personalizados SET rotulo=:r, opcoes=:o, obrigatorio=:ob, ativo=:a, ordem=:ord WHERE id=:i"),
               {"r": ch.get("rotulo", atual["rotulo"]),
                "o": json.dumps(ch["opcoes"]) if ch.get("opcoes") else atual["opcoes"],
                "ob": int(ch.get("obrigatorio", bool(atual["obrigatorio"]))), "a": int(ch.get("ativo", bool(atual["ativo"]))),
                "ord": ch.get("ordem", atual["ordem"]), "i": campo_id})
    audit.record(db, request, "campo_editado", user_id=user.user_id, username=user.username,
                 entity_type="campo_personalizado", entity_id=campo_id, details={"campos": sorted(ch)})
    db.commit()
    return _campo(db.execute(text("SELECT * FROM campos_personalizados WHERE id = :i"), {"i": campo_id}).mappings().one())


@router.delete("/campos/{campo_id}", status_code=204)
def excluir_campo(campo_id: int, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    if db.execute(text("SELECT 1 FROM empresa_campos_valores WHERE campo_id = :i LIMIT 1"), {"i": campo_id}).first():
        raise HTTPException(status_code=409, detail="Há empresas com valor neste campo. Desative-o em vez de excluir.")
    n = db.execute(text("DELETE FROM campos_personalizados WHERE id = :i"), {"i": campo_id}).rowcount
    if not n:
        db.rollback()
        raise HTTPException(status_code=404, detail="Campo não encontrado.")
    audit.record(db, request, "campo_excluido", user_id=user.user_id, username=user.username,
                 entity_type="campo_personalizado", entity_id=campo_id)
    db.commit()
