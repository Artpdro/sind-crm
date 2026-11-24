"""Mapa: lê APENAS coordenadas já gravadas no banco. Nada aqui chama o geocodificador (regra obrigatória do projeto)."""
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import get_db
from app.modules.companies import service as csvc
from app.modules.companies.filters import Filtros, filtros_query, montar
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api/mapa", tags=["mapa"])
F = dict(allow_inf_nan=False)


def _bbox(sul, oeste, norte, leste):
    dados = (sul, oeste, norte, leste)
    if all(v is None for v in dados):
        return None
    if any(v is None for v in dados):
        raise HTTPException(status_code=422, detail="Informe sul, oeste, norte e leste juntos.")
    if sul > norte or oeste > leste:
        raise HTTPException(status_code=422, detail="Área inválida.")
    return dados


@router.get("/empresas")
def marcadores(f: Filtros = Depends(filtros_query),
               sul: float | None = Query(None, ge=-90, le=90, **F), oeste: float | None = Query(None, ge=-180, le=180, **F),
               norte: float | None = Query(None, ge=-90, le=90, **F), leste: float | None = Query(None, ge=-180, le=180, **F),
               user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    """Contadores da busca + marcadores (id, coordenadas e cor) das empresas que TÊM localização. Detalhes vêm ao clicar."""
    area = _bbox(sul, oeste, norte, leste)
    w, p = montar(db, f)
    total, com_loc = db.execute(text("SELECT count(*), count(e.latitude) FROM empresas e WHERE " + w), p).one()  # nosec B608
    r: dict = {"total": total, "com_localizacao": com_loc, "sem_localizacao": total - com_loc}
    if f.paridade != "todos":  # transparência: quantas ficaram de fora por não terem número identificável (S/N, Fundos...)
        w2, p2 = montar(db, f, ignorar_paridade=True)
        r["excluidos_sem_numero"] = db.execute(text("SELECT count(*) FROM empresas e WHERE " + w2 + " AND e.numero_base IS NULL"), p2).scalar_one()  # nosec B608
    lim = get_settings().max_marcadores
    sql = ("SELECT e.id, e.latitude AS lat, e.longitude AS lon, e.geocode_status, e.geocode_desatualizada, e.geocode_precisao, s.cor "
           "FROM empresas e LEFT JOIN situacoes s ON s.id = e.situacao_id WHERE " + w + " AND e.latitude IS NOT NULL")  # nosec B608
    if area:
        sql += " AND e.latitude BETWEEN :sul AND :norte AND e.longitude BETWEEN :oeste AND :leste"
        p = {**p, "sul": area[0], "oeste": area[1], "norte": area[2], "leste": area[3]}
    rows = db.execute(text(sql + " ORDER BY e.id LIMIT :lim"), {**p, "lim": lim + 1}).mappings().all()
    r["truncado"] = len(rows) > lim
    r["marcadores"] = [{"id": x["id"], "lat": x["lat"], "lon": x["lon"], "cor": x["cor"] or "#6b7280",
                        "revisao": x["geocode_status"] == "revisao", "desatualizada": bool(x["geocode_desatualizada"]),
                        "aprox": x["geocode_precisao"] in ("interpolado", "rua", "cep", "bairro")} for x in rows[:lim]]
    return r


@router.get("/empresas/{empresa_id}")
def detalhe(empresa_id: int, user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    """Dados do popup do marcador (sem emails/telefones: o mínimo necessário)."""
    e = csvc.carregar(db, empresa_id)
    if not e:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    chaves = ("id", "nome", "nome_fantasia", "cnpj", "rua", "numero", "complemento", "bairro", "cidade", "estado", "cep",
              "situacao_nome", "situacao_cor", "porte_nome", "tipo_nome", "categorias", "geocode_status", "precisa_revisao")
    return {k: e[k] for k in chaves}


@router.get("/proximas")
def proximas(lat: float = Query(..., ge=-90, le=90, **F), lon: float = Query(..., ge=-180, le=180, **F),
             raio_m: int = Query(1000, ge=1, le=20000), limite: int = Query(50, ge=1, le=200),
             f: Filtros = Depends(filtros_query), user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    """Empresas num raio ao redor de um ponto (mais perto primeiro). Pré-filtro por caixa (usa o índice) + distância exata."""
    if not ratelimit.allow(db, "mapa_proximas", str(user.user_id), 120, 60):
        raise HTTPException(status_code=429, detail="Muitas consultas. Aguarde um instante.")
    dlat = raio_m / 111_320
    dlon = raio_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
    w, p = montar(db, f)
    rows = db.execute(text(
        "SELECT e.id, e.nome, e.rua, e.numero, e.bairro, e.cidade, e.latitude AS lat, e.longitude AS lon, "
        "distancia_m(e.latitude, e.longitude, :lat, :lon) AS distancia_m FROM empresas e WHERE " + w +  # nosec B608
        " AND e.latitude BETWEEN :a AND :b AND e.longitude BETWEEN :c AND :d AND distancia_m(e.latitude, e.longitude, :lat, :lon) <= :raio "
        "ORDER BY distancia_m, e.id LIMIT :lim"),
        {**p, "lat": lat, "lon": lon, "a": lat - dlat, "b": lat + dlat, "c": lon - dlon, "d": lon + dlon, "raio": raio_m, "lim": limite}).mappings().all()
    return {"centro": {"lat": lat, "lon": lon}, "raio_m": raio_m,
            "itens": [{**dict(r), "distancia_m": round(r["distancia_m"], 1)} for r in rows]}
