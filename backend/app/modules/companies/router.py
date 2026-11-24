import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app import timeutil
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.companies import normalizers as nz
from app.modules.companies import service as svc
from app.modules.geocoding.auto import auto
from app.modules.companies.filters import Filtros, filtros_query, montar
from app.modules.companies.schemas import EmpresaCreate, EmpresaUpdate, InteracaoIn
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api/empresas", tags=["empresas"])
pode_escrever = sessions.require_role("admin", "operador")

INSERT_SQL = (
    "INSERT INTO empresas (nome, nome_fantasia, cnpj, email, emails_adicionais, celular, fone, cep, rua, numero, "
    "numero_base, complemento, bairro, cidade, estado, situacao_id, porte_id, tipo_empresa_id, informacoes_adicionais, "
    "created_by, updated_by) VALUES (:nome, :nome_fantasia, :cnpj, :email, :emails_adicionais, :celular, :fone, :cep, "
    ":rua, :numero, :numero_base, :complemento, :bairro, :cidade, :estado, :situacao_id, :porte_id, :tipo_empresa_id, "
    ":informacoes_adicionais, :uid, :uid) RETURNING id"
)
UPDATE_SQL = (
    "UPDATE empresas SET nome=:nome, nome_fantasia=:nome_fantasia, cnpj=:cnpj, email=:email, "
    "emails_adicionais=:emails_adicionais, celular=:celular, fone=:fone, cep=:cep, rua=:rua, numero=:numero, "
    "numero_base=:numero_base, complemento=:complemento, bairro=:bairro, cidade=:cidade, estado=:estado, "
    "situacao_id=:situacao_id, porte_id=:porte_id, tipo_empresa_id=:tipo_empresa_id, "
    "informacoes_adicionais=:informacoes_adicionais, geocode_desatualizada=:desatualizada, "
    "version=version+1, updated_by=:uid WHERE id=:id AND version=:v AND deleted_at IS NULL"
)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ───────── Consulta por CNPJ (antes de /{id}) ─────────
@router.get("/cnpj/{cnpj:path}")  # :path aceita o CNPJ formatado, que contém "/"
def consultar_cnpj(cnpj: str, user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    if len(cnpj) > 30:
        raise HTTPException(status_code=422, detail="CNPJ inválido.")
    if not ratelimit.allow(db, "cnpj_query", str(user.user_id), 120, 60):
        raise HTTPException(status_code=429, detail="Muitas consultas. Aguarde um instante.")
    try:
        normalizado = nz.normalize_cnpj(cnpj)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not normalizado:
        raise HTTPException(status_code=422, detail="Informe o CNPJ.")
    row = db.execute(text("SELECT id FROM empresas WHERE cnpj = :c AND deleted_at IS NULL"), {"c": normalizado}).first()
    if not row:
        return JSONResponse({"encontrada": False, "cnpj": normalizado, "detail": "Empresa não encontrada."}, status_code=404)
    return {"encontrada": True, "empresa": svc.carregar(db, row[0])}


# ───────── Listagem paginada ─────────
@router.get("")
def listar(f: Filtros = Depends(filtros_query), page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
           user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    w, p = montar(db, f)  # fragmentos fixos; valores sempre como parâmetros
    total = db.execute(text("SELECT count(*) FROM empresas e WHERE " + w), p).scalar_one()  # nosec B608
    rows = db.execute(text(svc.BASE_SELECT + "WHERE " + w +  # nosec B608
                           " ORDER BY e.nome COLLATE NOCASE, e.id LIMIT :lim OFFSET :off"),
                      {**p, "lim": page_size, "off": (page - 1) * page_size}).mappings().all()
    itens = [svc._base(r) for r in rows]
    cats = svc.categorias_de(db, [i["id"] for i in itens])
    for i in itens:
        i["categorias"] = cats.get(i["id"], [])
    return {"total": total, "page": page, "page_size": page_size, "itens": itens}


# ───────── Criar ─────────
@router.post("", status_code=201)
def criar(body: EmpresaCreate, request: Request, user=Depends(pode_escrever), db: Connection = Depends(get_db)):
    d = body.model_dump()
    svc.checar_referencias(db, porte_id=d["porte_id"], tipo_empresa_id=d["tipo_empresa_id"],
                           situacao_id=d["situacao_id"], categoria_ids=d["categoria_ids"])
    if d["cnpj"]:
        ja = db.execute(text("SELECT id FROM empresas WHERE cnpj = :c"), {"c": d["cnpj"]}).first()
        if ja:
            return JSONResponse({"detail": "Já existe uma empresa com este CNPJ.", "empresa_id": ja[0]}, status_code=409)
    params = {k: d[k] for k in svc.EDITABLE}
    params.update(emails_adicionais=json.dumps(d["emails_adicionais"] or []), numero_base=nz.numero_base(d["numero"]), uid=user.user_id)
    try:
        eid = db.execute(text(INSERT_SQL), params).scalar_one()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"detail": "Já existe uma empresa com este CNPJ."}, status_code=409)
    svc.salvar_categorias(db, eid, d["categoria_ids"] or [])
    svc.aplicar_campos(db, eid, d["campos"], criando=True)
    audit.record(db, request, "empresa_criada", user_id=user.user_id, username=user.username, entity_type="empresa", entity_id=eid)
    db.commit()
    auto.acordar()  # empresa nova: entra na fila e já é geocodificada (aparece no mapa sozinha)
    return svc.carregar(db, eid)


# ───────── Ver / editar / excluir ─────────
@router.get("/{empresa_id}")
def ver(empresa_id: int, user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    e = svc.carregar(db, empresa_id)
    if not e:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return e


@router.patch("/{empresa_id}")
def editar(empresa_id: int, body: EmpresaUpdate, request: Request, user=Depends(pode_escrever),
           db: Connection = Depends(get_db)):
    atual = svc.carregar(db, empresa_id)
    if not atual:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    if body.version != atual["version"]:
        raise HTTPException(status_code=409, detail="Esta empresa foi alterada por outra pessoa. Recarregue antes de editar.")
    mudancas = body.model_dump(exclude_unset=True)
    mudancas.pop("version")
    if "nome" in mudancas and mudancas["nome"] is None:
        raise HTTPException(status_code=422, detail="O nome da empresa é obrigatório.")
    campos, cats = mudancas.pop("campos", None), mudancas.pop("categoria_ids", None)
    emails = mudancas.pop("emails_adicionais", None)
    novo = {k: mudancas.get(k, atual[k]) for k in svc.EDITABLE}
    svc.checar_referencias(db, porte_id=novo["porte_id"] if "porte_id" in mudancas else None,
                           tipo_empresa_id=novo["tipo_empresa_id"] if "tipo_empresa_id" in mudancas else None,
                           situacao_id=novo["situacao_id"] if "situacao_id" in mudancas else None,
                           categoria_ids=cats)
    if novo["cnpj"] and novo["cnpj"] != atual["cnpj"]:
        if db.execute(text("SELECT 1 FROM empresas WHERE cnpj = :c AND id <> :i"), {"c": novo["cnpj"], "i": empresa_id}).first():
            return JSONResponse({"detail": "Já existe outra empresa com este CNPJ."}, status_code=409)
    endereco_mudou = any(novo[k] != atual[k] for k in svc.ADDRESS)
    # Mudou o endereço: a localização atual pode estar desatualizada. As coordenadas anteriores são PRESERVADAS
    # até uma nova geocodificação ser validada (Fase 4).
    desatualizada = atual["geocode_desatualizada"] or (endereco_mudou and atual["latitude"] is not None)
    params = {**novo, "emails_adicionais": json.dumps(emails if emails is not None else atual["emails_adicionais"]),
              "numero_base": nz.numero_base(novo["numero"]), "desatualizada": int(desatualizada),
              "uid": user.user_id, "id": empresa_id, "v": body.version}
    try:
        n = db.execute(text(UPDATE_SQL), params).rowcount
    except IntegrityError:
        db.rollback()
        return JSONResponse({"detail": "Já existe outra empresa com este CNPJ."}, status_code=409)
    if n != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Esta empresa foi alterada por outra pessoa. Recarregue antes de editar.")
    if endereco_mudou:  # volta para a fila de geocodificação (as coordenadas atuais são preservadas até haver outras válidas)
        db.execute(text("UPDATE empresas SET geocode_status = 'pendente', geocode_erro = NULL WHERE id = :i"), {"i": empresa_id})
    if cats is not None:
        svc.salvar_categorias(db, empresa_id, cats)
    extras = svc.aplicar_campos(db, empresa_id, campos, criando=False) if campos else []
    alterados = [k for k in svc.EDITABLE if novo[k] != atual[k]] + extras + (["categorias"] if cats is not None else []) \
        + (["emails_adicionais"] if emails is not None and emails != atual["emails_adicionais"] else [])
    # LGPD: a auditoria guarda QUAIS campos mudaram, não os valores.
    audit.record(db, request, "empresa_editada", user_id=user.user_id, username=user.username, entity_type="empresa",
                 entity_id=empresa_id, details={"campos": alterados, "endereco_alterado": endereco_mudou})
    if novo["situacao_id"] != atual["situacao_id"]:
        audit.record(db, request, "situacao_alterada", user_id=user.user_id, username=user.username, entity_type="empresa",
                     entity_id=empresa_id, details={"de": atual["situacao_id"], "para": novo["situacao_id"]})
    db.commit()
    if endereco_mudou:
        auto.acordar()
    return svc.carregar(db, empresa_id)


@router.post("/{empresa_id}/revisado")
def marcar_revisada(empresa_id: int, request: Request, user=Depends(pode_escrever), db: Connection = Depends(get_db)):
    """Tira a marca de 'precisa de revisão' (empresas vindas da importação com dado faltando ou duvidoso)."""
    if not svc.carregar(db, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    n = db.execute(text("UPDATE empresas SET precisa_revisao = 0, motivos_revisao = '[]', version = version + 1, updated_by = :u "
                        "WHERE id = :i AND precisa_revisao = 1"), {"u": user.user_id, "i": empresa_id}).rowcount
    if n:
        audit.record(db, request, "empresa_revisada", user_id=user.user_id, username=user.username, entity_type="empresa", entity_id=empresa_id)
    db.commit()
    return svc.carregar(db, empresa_id)


@router.delete("/{empresa_id}", status_code=204)
def excluir(empresa_id: int, request: Request, user=Depends(sessions.require_role("admin")),
            db: Connection = Depends(get_db)):
    """Exclusão lógica: a empresa some das telas, mas o histórico de interações é preservado."""
    n = db.execute(text("UPDATE empresas SET deleted_at = :now, updated_by = :u WHERE id = :i AND deleted_at IS NULL"),
                   {"now": timeutil.now(), "u": user.user_id, "i": empresa_id}).rowcount
    if not n:
        db.rollback()
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    audit.record(db, request, "empresa_excluida", user_id=user.user_id, username=user.username, entity_type="empresa", entity_id=empresa_id)
    db.commit()


# ───────── Histórico de interações (somente criar e listar) ─────────
@router.get("/{empresa_id}/interacoes")
def listar_interacoes(empresa_id: int, page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
                      user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    if not db.execute(text("SELECT 1 FROM empresas WHERE id = :i AND deleted_at IS NULL"), {"i": empresa_id}).first():
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    total = db.execute(text("SELECT count(*) FROM interacoes WHERE empresa_id = :e"), {"e": empresa_id}).scalar_one()
    rows = db.execute(text(
        "SELECT i.id, i.tipo, i.descricao, i.observacoes, i.data_interacao, u.username AS usuario, "
        "s.nome AS situacao_resultante FROM interacoes i JOIN users u ON u.id = i.usuario_id "
        "LEFT JOIN situacoes s ON s.id = i.situacao_resultante_id WHERE i.empresa_id = :e "
        "ORDER BY i.data_interacao DESC, i.id DESC LIMIT :l OFFSET :o"),
        {"e": empresa_id, "l": page_size, "o": (page - 1) * page_size}).mappings().all()
    return {"total": total, "page": page, "page_size": page_size, "itens": [dict(r) for r in rows]}


@router.post("/{empresa_id}/interacoes", status_code=201)
def criar_interacao(empresa_id: int, body: InteracaoIn, request: Request, user=Depends(pode_escrever),
                    db: Connection = Depends(get_db)):
    emp = db.execute(text("SELECT id, situacao_id FROM empresas WHERE id = :i AND deleted_at IS NULL"), {"i": empresa_id}).mappings().first()
    if not emp:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    if body.situacao_resultante_id is not None:
        svc.checar_referencias(db, porte_id=None, tipo_empresa_id=None, situacao_id=body.situacao_resultante_id, categoria_ids=None)
    # O responsável é SEMPRE o usuário da sessão (nunca vem do corpo da requisição).
    iid = db.execute(text(
        "INSERT INTO interacoes (empresa_id, usuario_id, tipo, descricao, situacao_resultante_id, observacoes, data_interacao) "
        "VALUES (:e, :u, :t, :d, :s, :o, :now) RETURNING id"),
        {"e": empresa_id, "u": user.user_id, "t": body.tipo, "d": body.descricao, "s": body.situacao_resultante_id,
         "o": body.observacoes, "now": timeutil.now()}).scalar_one()
    if body.situacao_resultante_id is not None and body.situacao_resultante_id != emp["situacao_id"]:
        db.execute(text("UPDATE empresas SET situacao_id = :s, version = version + 1, updated_by = :u WHERE id = :i"),
                   {"s": body.situacao_resultante_id, "u": user.user_id, "i": empresa_id})
        audit.record(db, request, "situacao_alterada", user_id=user.user_id, username=user.username, entity_type="empresa",
                     entity_id=empresa_id, details={"de": emp["situacao_id"], "para": body.situacao_resultante_id, "via": "interacao"})
    audit.record(db, request, "interacao_criada", user_id=user.user_id, username=user.username, entity_type="empresa",
                 entity_id=empresa_id, details={"interacao_id": iid, "tipo": body.tipo})
    db.commit()
    return {"id": iid}
