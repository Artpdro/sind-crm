"""GET /api/receita/situacao/{cnpj}: situação cadastral (ativa / inativa / inapta) na Receita Federal.
Qualquer usuário logado pode consultar (é informação pública). Resultado em cache por poucas horas: poupa o serviço
externo e acelera; só o que vale a pena guardar (CNPJ + situação) é gravado."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.companies.normalizers import normalize_cnpj
from app.modules.receita import client
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api/receita", tags=["receita"])

FONTE = "Receita Federal"
FMT = "%Y-%m-%d %H:%M:%S"


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


@router.get("/situacao/{cnpj}")
def situacao(request: Request, cnpj: str = Path(max_length=30), user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    s = get_settings()
    if not s.receita_ativo:
        raise HTTPException(status_code=503, detail="A consulta à Receita Federal está desativada neste sistema.")
    try:
        c14 = normalize_cnpj(cnpj)
    except ValueError:
        raise HTTPException(status_code=422, detail="CNPJ inválido.")
    if not c14:
        raise HTTPException(status_code=422, detail="Informe o CNPJ.")

    limite = (_agora() - timedelta(hours=s.receita_cache_horas)).strftime(FMT)
    hit = db.execute(text("SELECT situacao, descricao, consultado_em FROM receita_cache WHERE cnpj = :c AND consultado_em >= :l"),
                     {"c": c14, "l": limite}).mappings().first()
    if hit:
        return {"cnpj": c14, "situacao": hit["situacao"], "descricao": hit["descricao"], "consultado_em": hit["consultado_em"],
                "fonte": FONTE, "do_cache": True}

    if not ratelimit.allow(db, "receita", str(user.user_id), 60, 3600):
        db.commit()
        raise HTTPException(status_code=429, detail="Muitas consultas à Receita em pouco tempo. Aguarde alguns minutos.")
    db.commit()  # registra a tentativa mesmo se a consulta externa falhar
    try:
        r = client.consultar(c14, s.receita_url, s.receita_timeout)
    except client.CnpjNaoEncontrado:
        raise HTTPException(status_code=404, detail="CNPJ não encontrado na base da Receita Federal.")
    except client.ReceitaIndisponivel:
        raise HTTPException(status_code=503, detail="Não foi possível consultar a Receita Federal agora. Tente novamente em instantes.")
    agora = _agora().strftime(FMT)
    db.execute(text("INSERT INTO receita_cache (cnpj, situacao, descricao, consultado_em) VALUES (:c, :s, :d, :q) "
                    "ON CONFLICT(cnpj) DO UPDATE SET situacao = excluded.situacao, descricao = excluded.descricao, consultado_em = excluded.consultado_em"),
               {"c": c14, "s": r.situacao, "d": r.descricao, "q": agora})
    audit.record(db, request, "consulta_receita", user_id=user.user_id, username=user.username, details={"situacao": r.situacao})  # nunca o CNPJ
    db.commit()
    return {"cnpj": c14, "situacao": r.situacao, "descricao": r.descricao, "consultado_em": agora, "fonte": FONTE, "do_cache": False}
