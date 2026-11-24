from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy import Connection, text

from app import timeutil
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.auth.schemas import Strict
from app.modules.geocoding import service as svc
from app.modules.geocoding.auto import auto
from app.modules.geocoding.client import GeocoderConfigInvalida
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api", tags=["geocodificacao"])
pode_escrever = sessions.require_role("admin", "operador")
admin = sessions.require_role("admin")


class LoteIn(Strict):
    limite: int = Field(default=10, ge=1, le=25)
    retentar_falhas: bool = False


class ReprocessarIn(Strict):
    escopo: Literal["nao_encontradas", "revisao", "todas"]


class LocalizacaoIn(Strict):
    latitude: float = Field(allow_inf_nan=False, ge=-90, le=90)
    longitude: float = Field(allow_inf_nan=False, ge=-180, le=180)

    @field_validator("longitude")
    @classmethod
    def _no_brasil(cls, v, info):
        lat = info.data.get("latitude")
        if lat is not None and not svc.no_brasil(lat, v):
            raise ValueError("A localização precisa estar dentro do Brasil.")
        return v


@router.get("/geocodificacao/resumo")
def resumo(user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    return svc.resumo(db)


@router.get("/geocodificacao/diagnostico")
def diagnostico(user=Depends(pode_escrever), db: Connection = Depends(get_db)):
    """Por que as empresas não foram mapeadas / foram mapeadas só de forma aproximada. Também mostra o último erro do serviço externo."""
    return svc.diagnostico(db)


@router.post("/geocodificacao/testar")
def testar(request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    """Faz UMA consulta de teste ao serviço (prova se há internet / se o serviço está bloqueando este computador)."""
    if not ratelimit.allow(db, "geocode_teste", str(user.user_id), 20, 3600):
        raise HTTPException(status_code=429, detail="Muitos testes. Aguarde antes de tentar de novo.")
    try:
        return svc.testar_conexao(svc.criar_geocoder())
    except GeocoderConfigInvalida:
        raise HTTPException(status_code=503, detail="A configuração do geocodificador é inválida. Verifique GEOCODER_URL.")


@router.post("/geocodificacao/reprocessar")
def reprocessar(body: ReprocessarIn, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    """Devolve empresas à fila para tentar de novo com o motor atual (coordenadas existentes ficam até haver outras; manuais nunca)."""
    n = svc.reprocessar(db, body.escopo)
    audit.record(db, request, "geocodificacao_reprocessar", user_id=user.user_id, username=user.username, details={"escopo": body.escopo, "empresas": n})
    db.commit()
    auto.acordar()
    return {"empresas_na_fila": n, **svc.resumo(db)}


@router.post("/geocodificacao/lote")
def lote(body: LoteIn, request: Request, user=Depends(admin), db: Connection = Depends(get_db)):
    """Geocodifica até N empresas pendentes (as consultas saem do SERVIDOR, a 1 por segundo). O mapa nunca faz isso."""
    if not ratelimit.allow(db, "geocode_lote", str(user.user_id), 400, 3600):
        raise HTTPException(status_code=429, detail="Muitos lotes. Aguarde antes de continuar.")
    try:
        geo = svc.criar_geocoder()
        r = svc.processar_lote(db, geo, body.limite, retentar_falhas=body.retentar_falhas)
    except GeocoderConfigInvalida:
        raise HTTPException(status_code=503, detail="A configuração do geocodificador é inválida. Verifique GEOCODER_URL.")
    if r.get("ocupado"):
        raise HTTPException(status_code=409, detail="Já existe uma geocodificação em andamento (pode ser a automática do servidor).")
    audit.record(db, request, "geocodificacao_lote", user_id=user.user_id, username=user.username,
                 details={k: v for k, v in r.items() if k != "ultimo_id"})
    db.commit()
    return {**r, **svc.resumo(db)}


@router.put("/empresas/{empresa_id}/localizacao")
def corrigir_localizacao(empresa_id: int, body: LocalizacaoIn, request: Request, user=Depends(pode_escrever),
                         db: Connection = Depends(get_db)):
    """Correção manual: vale como localização validada e substitui a anterior."""
    n = db.execute(text("UPDATE empresas SET latitude=:la, longitude=:lo, geocode_status='localizado', geocode_precisao='manual', geocode_fonte='manual', "
                        "geocode_endereco_usado='manual', geocoded_at=:now, geocode_desatualizada=0, geocode_erro=NULL, "
                        "version=version+1, updated_by=:u WHERE id=:i AND deleted_at IS NULL"),
                   {"la": body.latitude, "lo": body.longitude, "now": timeutil.now(), "u": user.user_id, "i": empresa_id}).rowcount
    if not n:
        db.rollback()
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    audit.record(db, request, "localizacao_corrigida_manualmente", user_id=user.user_id, username=user.username,
                 entity_type="empresa", entity_id=empresa_id)
    db.commit()
    return {"id": empresa_id, "latitude": body.latitude, "longitude": body.longitude, "geocode_status": "localizado"}


@router.post("/empresas/{empresa_id}/geocodificar")
def solicitar_nova_geocodificacao(empresa_id: int, request: Request, user=Depends(pode_escrever), db: Connection = Depends(get_db)):
    """Marca para nova geocodificação (feita no próximo lote). A localização atual é preservada até haver outra válida."""
    n = db.execute(text("UPDATE empresas SET geocode_status='pendente', geocode_erro=NULL, geocode_tentativas=0, "
                        "geocode_desatualizada = CASE WHEN latitude IS NOT NULL THEN 1 ELSE geocode_desatualizada END "
                        "WHERE id=:i AND deleted_at IS NULL"), {"i": empresa_id}).rowcount
    if not n:
        db.rollback()
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    svc.esquecer_negativos(db, svc.carregar(db, empresa_id))
    audit.record(db, request, "geocodificacao_solicitada", user_id=user.user_id, username=user.username,
                 entity_type="empresa", entity_id=empresa_id)
    db.commit()
    auto.acordar()
    return {"id": empresa_id, "geocode_status": "pendente"}
