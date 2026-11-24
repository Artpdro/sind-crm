import hashlib
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import Connection, text

from app import timeutil
from app.config import get_settings
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.auth.schemas import Strict
from app.modules.geocoding.auto import auto
from app.modules.importacao import service as svc
from app.modules.importacao.parser import ArquivoInvalido, ler_planilha
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api/importacoes", tags=["importacao"])
admin = sessions.require_role("admin")  # importar altera muitos dados de uma vez: só administrador

MIMES_XLSX = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel", "application/octet-stream", ""}


class ConfirmarIn(Strict):
    modo: Literal["novos", "atualizar", "ambos"]


def _nome_seguro(nome: str | None) -> str:
    base = re.split(r"[\\/]", nome or "")[-1]  # sem caminhos; o nome só serve para exibição, nunca vira caminho de disco
    base = "".join(c for c in base if ord(c) >= 32 and c not in '<>:"|?*')[:100].strip()
    return base or "planilha.xlsx"


def _get(db: Connection, imp_id: int):
    row = db.execute(text("SELECT * FROM importacoes WHERE id = :i"), {"i": imp_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    return row


def _out(row) -> dict:
    d = dict(row)
    d["resumo"] = json.loads(d["resumo"] or "{}")
    d["resultado"] = json.loads(d["resultado"]) if d["resultado"] else None
    d.pop("sha256", None)
    return d


def _limpar_antigas(db: Connection) -> None:
    db.execute(text("UPDATE importacoes SET status='cancelada', concluida_em=:now WHERE status='previa' AND criada_em < :cut"),
               {"now": timeutil.now(), "cut": timeutil.now(hours=-24)})
    db.execute(text("DELETE FROM importacao_linhas WHERE importacao_id IN (SELECT id FROM importacoes WHERE status <> 'previa')"))


@router.post("", status_code=201)
def enviar(request: Request, arquivo: UploadFile = File(...), user=Depends(admin), db: Connection = Depends(get_db)):
    s = get_settings()
    if not ratelimit.allow(db, "import_upload", str(user.user_id), 10, 3600):
        raise HTTPException(status_code=429, detail="Muitos envios. Aguarde antes de tentar de novo.")
    nome = _nome_seguro(arquivo.filename)
    low = nome.lower()
    if not low.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Envie uma planilha do Excel (.xlsx ou .xls).")
    if (arquivo.content_type or "") not in MIMES_XLSX:
        raise HTTPException(status_code=422, detail="Tipo de arquivo não aceito.")
    data = arquivo.file.read(s.max_upload_bytes + 1)
    if len(data) > s.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Arquivo maior que o limite de {s.max_upload_bytes // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(status_code=422, detail="O arquivo está vazio.")
    try:
        plan = ler_planilha(data)
    except ArquivoInvalido as e:
        audit.record(db, request, "importacao_arquivo_recusado", user_id=user.user_id, username=user.username, details={"tamanho": len(data)})
        db.commit()
        raise HTTPException(status_code=422, detail=str(e))
    _limpar_antigas(db)
    itens = svc.classificar(db, plan)
    iid, resumo = svc.persistir(db, user.user_id, nome, hashlib.sha256(data).hexdigest(), len(data), plan, itens)
    audit.record(db, request, "importacao_enviada", user_id=user.user_id, username=user.username, entity_type="importacao",
                 entity_id=iid, details={"linhas": resumo["linhas_lidas"], "tamanho": len(data)})
    db.commit()
    return _out(_get(db, iid))


@router.get("")
def historico(user=Depends(admin), db: Connection = Depends(get_db)):
    rows = db.execute(text("SELECT i.*, u.username AS usuario FROM importacoes i JOIN users u ON u.id = i.usuario_id "
                           "ORDER BY i.id DESC LIMIT 50")).mappings().all()
    return [_out(r) | {"usuario": r["usuario"]} for r in rows]


@router.get("/{imp_id}")
def ver(imp_id: int, user=Depends(admin), db: Connection = Depends(get_db)):
    return _out(_get(db, imp_id))


@router.get("/{imp_id}/linhas")
def linhas(imp_id: int, acao: Literal["novo", "atualizar", "sem_alteracao", "duplicada", "rejeitada", "ja_existente"] | None = None,
           somente_revisao: bool = False, page: int = Query(1, ge=1, le=10000), page_size: int = Query(50, ge=1, le=200),
           user=Depends(admin), db: Connection = Depends(get_db)):
    if _get(db, imp_id)["status"] != "previa":
        raise HTTPException(status_code=409, detail="Esta prévia já foi encerrada.")
    p = {"i": imp_id, "a": acao, "r": int(somente_revisao), "l": page_size, "o": (page - 1) * page_size}
    filtro = "importacao_id = :i AND (:a IS NULL OR acao = :a) AND (:r = 0 OR revisao = 1)"
    total = db.execute(text("SELECT count(*) FROM importacao_linhas WHERE " + filtro), p).scalar_one()  # nosec B608 - filtro fixo
    rows = db.execute(text("SELECT linha, acao, revisao, dados, motivos, avisos, erros, alteracoes, mantida_linha, empresa_id "
                           "FROM importacao_linhas WHERE " + filtro + " ORDER BY linha LIMIT :l OFFSET :o"), p).mappings().all()  # nosec B608
    itens = []
    for r in rows:
        d = json.loads(r["dados"])
        itens.append({"linha": r["linha"], "acao": r["acao"], "revisao": bool(r["revisao"]),
                      "nome": d.get("nome"), "cnpj": d.get("cnpj") or d.get("cnpj_original"), "cidade": d.get("cidade"),
                      "motivos": json.loads(r["motivos"]), "avisos": json.loads(r["avisos"]), "erros": json.loads(r["erros"]),
                      "alteracoes": json.loads(r["alteracoes"]), "mantida_linha": r["mantida_linha"], "empresa_id": r["empresa_id"]})
    return {"total": total, "page": page, "page_size": page_size, "itens": itens}


@router.post("/{imp_id}/confirmar")
def confirmar(imp_id: int, body: ConfirmarIn, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    if not ratelimit.allow(db, "import_confirm", str(user.user_id), 10, 3600):
        raise HTTPException(status_code=429, detail="Muitas confirmações. Aguarde antes de tentar de novo.")
    _get(db, imp_id)
    # Só UMA confirmação vence (mesmo com cliques/requisições simultâneas): a troca de status é atômica.
    n = db.execute(text("UPDATE importacoes SET status='executando', modo=:m WHERE id=:i AND status='previa'"),
                   {"m": body.modo, "i": imp_id}).rowcount
    if n != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Esta importação já foi confirmada ou cancelada.")
    try:
        res = svc.executar(db, imp_id, body.modo, user.user_id)
        db.execute(text("UPDATE importacoes SET status='concluida', resultado=:r, concluida_em=:now WHERE id=:i"),
                   {"r": json.dumps(res), "now": timeutil.now(), "i": imp_id})
        db.execute(text("DELETE FROM importacao_linhas WHERE importacao_id=:i"), {"i": imp_id})  # LGPD: não guardar a planilha
        audit.record(db, request, "importacao_confirmada", user_id=user.user_id, username=user.username,
                     entity_type="importacao", entity_id=imp_id, details={"modo": body.modo, **res})
        db.commit()
        auto.acordar()  # as empresas novas/alteradas entram na fila e passam a ser mapeadas
    except Exception:
        db.rollback()  # nada foi aplicado
        db.execute(text("UPDATE importacoes SET status='falhou', concluida_em=:now WHERE id=:i AND status='previa'"),
                   {"now": timeutil.now(), "i": imp_id})
        audit.record(db, request, "importacao_falhou", user_id=user.user_id, username=user.username, entity_type="importacao", entity_id=imp_id)
        db.commit()
        raise HTTPException(status_code=500, detail="A importação falhou e nenhuma empresa foi alterada.")
    return _out(_get(db, imp_id))


@router.post("/{imp_id}/cancelar")
def cancelar(imp_id: int, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    n = db.execute(text("UPDATE importacoes SET status='cancelada', concluida_em=:now WHERE id=:i AND status='previa'"),
                   {"now": timeutil.now(), "i": imp_id}).rowcount
    if n != 1:
        db.rollback()
        _get(db, imp_id)
        raise HTTPException(status_code=409, detail="Esta importação não está mais em prévia.")
    db.execute(text("DELETE FROM importacao_linhas WHERE importacao_id=:i"), {"i": imp_id})
    audit.record(db, request, "importacao_cancelada", user_id=user.user_id, username=user.username, entity_type="importacao", entity_id=imp_id)
    db.commit()
    return _out(_get(db, imp_id))
