"""Relatórios em PDF e resumo de atividades. Os filtros de empresas são os MESMOS da lista e do mapa (módulo `filters`):
o PDF traz exatamente o que a tela mostra. Valores sempre como parâmetros; fragmentos de SQL fixos."""
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.companies import service as csvc
from app.modules.companies.filters import Filtros, filtros_query, montar
from app.modules.relatorios import pdf, tiles
from app.security import ratelimit, sessions

router = APIRouter(prefix="/api/relatorios", tags=["relatorios"])
TIPOS = ("ligacao", "whatsapp", "email", "visita", "reuniao", "atendimento", "outro")
FUSO_H = 3  # Recife: UTC-3, sem horário de verão. As datas do banco são UTC.
MAX_DIAS = 366


def _agora() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=FUSO_H)).strftime("%d/%m/%Y %H:%M")


def _pdf_resposta(conteudo: bytes, prefixo: str) -> Response:
    nome = f"{prefixo}-{(datetime.now(timezone.utc) - timedelta(hours=FUSO_H)).strftime('%Y%m%d-%H%M')}.pdf"
    return Response(conteudo, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{nome}"', "Cache-Control": "no-store"})


def _nome(db: Connection, tabela: str, id_: int | None) -> str | None:
    if id_ is None:
        return None
    sql = {"situacoes": "SELECT nome FROM situacoes WHERE id = :i", "portes": "SELECT nome FROM portes WHERE id = :i",
           "tipos_empresa": "SELECT nome FROM tipos_empresa WHERE id = :i", "categorias": "SELECT nome FROM categorias WHERE id = :i"}[tabela]
    return db.execute(text(sql), {"i": id_}).scalar()


def _criterios_empresas(db: Connection, f: Filtros) -> list[tuple[str, str]]:
    c: list[tuple[str, str]] = []
    for rot, v in (("Nome/CNPJ", f.q), ("Rua", f.rua), ("Número", f.numero), ("Bairro", f.bairro), ("Cidade", f.cidade)):
        if v and v.strip():
            c.append((rot, v.strip()))
    if f.paridade != "todos":
        c.append(("Filtrar número por", "Apenas números pares" if f.paridade == "pares" else "Apenas números ímpares"))
    for rot, tab, v in (("Situação", "situacoes", f.situacao_id), ("Porte", "portes", f.porte_id), ("Tipo de empresa", "tipos_empresa", f.tipo_empresa_id), ("Categoria", "categorias", f.categoria_id)):
        if v is not None:
            c.append((rot, _nome(db, tab, v) or f"#{v}"))
    if f.revisao:
        c.append(("Cadastro", "Apenas empresas marcadas para revisão"))
    if f.geocode_status:
        c.append(("Localização", pdf.GEO.get(f.geocode_status, f.geocode_status)))
    for item in f.campos:
        chave, _, valor = item.partition(":")
        rot = db.execute(text("SELECT rotulo FROM campos_personalizados WHERE chave = :c"), {"c": chave}).scalar() or chave
        c.append((rot, valor))
    return c


def _titulo_empresas(f: Filtros) -> str:
    if f.rua and f.rua.strip():
        t = f"Relatório de empresas — {f.rua.strip()}"
        if f.numero and f.numero.strip():
            t += f", nº {f.numero.strip()}"
        if f.cidade and f.cidade.strip():
            t += f" ({f.cidade.strip()})"
        return t
    if f.numero and f.numero.strip():
        return f"Relatório de empresas — número {f.numero.strip()}"
    return "Relatório de empresas"


@router.get("/empresas.pdf")
def empresas_pdf(request: Request, f: Filtros = Depends(filtros_query), ordem: Literal["automatica", "nome", "endereco"] = Query("automatica"),
                 user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    """PDF das empresas da busca atual (mesmos filtros do mapa): critérios, mapa, dados de cada empresa e última interação."""
    if not ratelimit.allow(db, "pdf_empresas", str(user.user_id), 30, 3600):
        raise HTTPException(status_code=429, detail="Muitos relatórios em pouco tempo. Aguarde alguns minutos.")
    w, p = montar(db, f)
    total, com_loc = db.execute(text("SELECT count(*), count(e.latitude) FROM empresas e WHERE " + w), p).one()  # nosec B608
    if total == 0:
        raise HTTPException(status_code=404, detail="Nenhuma empresa encontrada para esta busca.")
    limite = get_settings().max_pdf_empresas
    if total > limite:
        raise HTTPException(status_code=422, detail=f"Esta busca tem {total} empresas e o PDF comporta até {limite}. Refine a busca (rua, bairro, situação…).")
    por_endereco = ordem == "endereco" or (ordem == "automatica" and any((f.rua, f.numero, f.bairro)))
    order = ("e.cidade COLLATE NOCASE, unaccent(e.rua), e.numero_base IS NULL, e.numero_base, e.numero, e.nome COLLATE NOCASE, e.id" if por_endereco
             else "e.nome COLLATE NOCASE, e.id")
    rows = db.execute(text(csvc.BASE_SELECT + "WHERE " + w + " ORDER BY " + order), p).mappings().all()  # nosec B608
    empresas = [csvc._base(r) for r in rows]
    ids = [e["id"] for e in empresas]
    cats: dict = {}
    for i in range(0, len(ids), 400):
        cats.update(csvc.categorias_de(db, ids[i:i + 400]))
    ult = {r["empresa_id"]: dict(r) for r in db.execute(text(
        "SELECT empresa_id, tipo, descricao, data_interacao, username AS usuario FROM ("
        " SELECT i.empresa_id, i.tipo, i.descricao, i.data_interacao, u.username, "
        " ROW_NUMBER() OVER (PARTITION BY i.empresa_id ORDER BY i.data_interacao DESC, i.id DESC) AS rn "
        " FROM interacoes i JOIN users u ON u.id = i.usuario_id "
        " WHERE i.empresa_id IN (SELECT e.id FROM empresas e WHERE " + w + ")) WHERE rn = 1"), p).mappings()}  # nosec B608
    for e in empresas:
        e["categorias"] = cats.get(e["id"], [])
        e["ultima_interacao"] = ult.get(e["id"])
    resumo = {"total": total, "com_localizacao": com_loc, "sem_localizacao": total - com_loc}
    if f.paridade != "todos":
        w2, p2 = montar(db, f, ignorar_paridade=True)
        resumo["excluidos_sem_numero"] = db.execute(text("SELECT count(*) FROM empresas e WHERE " + w2 + " AND e.numero_base IS NULL"), p2).scalar_one()  # nosec B608
    db.commit()  # não segura transação aberta durante o download dos blocos de mapa
    pontos = [(e["latitude"], e["longitude"]) for e in empresas if e["latitude"] is not None]
    base = tiles.montar_base(pontos) if get_settings().pdf_mapa_ruas else None
    aviso = "Não foi possível obter as ruas do mapa agora (sem internet ou serviço indisponível): exibindo o mapa esquemático." if pontos and get_settings().pdf_mapa_ruas and base is None else None
    conteudo = pdf.gerar(titulo=_titulo_empresas(f), criterios=_criterios_empresas(db, f), resumo=resumo, empresas=empresas, usuario=user.username, agora=_agora(),
                         base=base, aviso_mapa=aviso)
    audit.record(db, request, "relatorio_pdf_empresas", user_id=user.user_id, username=user.username,
                 details={"empresas": total, "filtros": sorted(k for k, v in vars(f).items() if v not in (None, "", [], "todos", False))})
    db.commit()
    return _pdf_resposta(conteudo, "relatorio-empresas")


# ───────── Relatório de atividades (por data) ─────────
def _periodo(inicio: date, fim: date) -> tuple[str, str]:
    """Dias locais (inclusive) -> intervalo UTC [ini, fim) no mesmo formato de texto do banco."""
    if fim < inicio:
        raise HTTPException(status_code=422, detail="A data final não pode ser anterior à inicial.")
    if (fim - inicio).days + 1 > MAX_DIAS:
        raise HTTPException(status_code=422, detail=f"O período máximo é de {MAX_DIAS} dias.")
    ini = datetime(inicio.year, inicio.month, inicio.day) + timedelta(hours=FUSO_H)
    end = datetime(fim.year, fim.month, fim.day) + timedelta(days=1, hours=FUSO_H)
    return ini.strftime("%Y-%m-%d %H:%M:%S.000"), end.strftime("%Y-%m-%d %H:%M:%S.000")


class _Atv:
    def __init__(self, inicio: date, fim: date, tipo: str | None, usuario_id: int | None, situacao_id: int | None, cidade: str | None):
        self.inicio, self.fim_dia, self.tipo, self.usuario_id, self.situacao_id, self.cidade = inicio, fim, tipo, usuario_id, situacao_id, cidade
        self.ini, self.fim = _periodo(inicio, fim)
        self.where = ["i.data_interacao >= :ini", "i.data_interacao < :fim", "e.deleted_at IS NULL"]
        self.p: dict = {"ini": self.ini, "fim": self.fim}
        if tipo:
            self.where.append("i.tipo = :tipo")
            self.p["tipo"] = tipo
        if usuario_id:
            self.where.append("i.usuario_id = :uid")
            self.p["uid"] = usuario_id
        if situacao_id:
            self.where.append("e.situacao_id = :sit")
            self.p["sit"] = situacao_id
        if cidade and cidade.strip():
            self.where.append("unaccent(e.cidade) = :cid")
            from app.textutil import sem_acento
            self.p["cid"] = sem_acento(cidade)
        self.w = " AND ".join(self.where)  # fragmentos fixos acima; valores em self.p
    FROM = "FROM interacoes i JOIN empresas e ON e.id = i.empresa_id JOIN users u ON u.id = i.usuario_id LEFT JOIN situacoes s ON s.id = i.situacao_resultante_id "


def _resumo_atv(db: Connection, a: _Atv) -> dict:
    t = db.execute(text("SELECT count(*), count(DISTINCT i.empresa_id), count(DISTINCT i.usuario_id) " + a.FROM + "WHERE " + a.w), a.p).one()  # nosec B608
    agrupa = lambda expr: {k: v for k, v in db.execute(text(f"SELECT {expr} AS k, count(*) {a.FROM} WHERE {a.w} GROUP BY 1 ORDER BY 2 DESC, 1"), a.p).all()}  # noqa: E731  # nosec B608
    return {"total": t[0], "empresas": t[1], "usuarios": t[2], "por_tipo": agrupa("i.tipo"), "por_usuario": agrupa("u.username"),
            "por_dia": dict(sorted(agrupa(f"strftime('%Y-%m-%d', datetime(substr(i.data_interacao, 1, 19), '-{FUSO_H} hours'))").items()))}


def _consulta_atv(inicio: date = Query(...), fim: date = Query(...), tipo: Literal[TIPOS] | None = Query(None), usuario_id: int | None = Query(None, ge=1),  # type: ignore[valid-type]
                  situacao_id: int | None = Query(None, ge=1), cidade: str | None = Query(None, max_length=150)) -> _Atv:
    return _Atv(inicio, fim, tipo, usuario_id, situacao_id, cidade)


@router.get("/usuarios")
def usuarios_com_atividade(user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    rows = db.execute(text("SELECT DISTINCT u.id, u.username FROM interacoes i JOIN users u ON u.id = i.usuario_id ORDER BY u.username")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/atividades")
def atividades(a: _Atv = Depends(_consulta_atv), page: int = Query(1, ge=1, le=100000), page_size: int = Query(50, ge=1, le=100),
               user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    """Prévia na tela do relatório por data: resumo + atividades do período (paginadas)."""
    rows = db.execute(text("SELECT i.id, i.data_interacao, i.tipo, i.descricao, u.username AS usuario, e.id AS empresa_id, e.nome, e.cnpj, e.cidade, s.nome AS situacao_resultante "  # nosec B608
                           + a.FROM + "WHERE " + a.w + " ORDER BY i.data_interacao DESC, i.id DESC LIMIT :lim OFFSET :off"),
                      {**a.p, "lim": page_size, "off": (page - 1) * page_size}).mappings().all()
    return {"resumo": _resumo_atv(db, a), "page": page, "page_size": page_size, "itens": [{**dict(r), "descricao": r["descricao"][:300]} for r in rows]}


@router.get("/atividades.pdf")
def atividades_pdf(request: Request, a: _Atv = Depends(_consulta_atv), user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    inicio, fim, tipo, usuario_id, situacao_id, cidade = a.inicio, a.fim_dia, a.tipo, a.usuario_id, a.situacao_id, a.cidade
    if not ratelimit.allow(db, "pdf_atividades", str(user.user_id), 30, 3600):
        raise HTTPException(status_code=429, detail="Muitos relatórios em pouco tempo. Aguarde alguns minutos.")
    lim = get_settings().max_pdf_atividades
    resumo = _resumo_atv(db, a)
    rows = db.execute(text("SELECT i.id, i.data_interacao, i.tipo, i.descricao, i.observacoes, u.username AS usuario, e.nome, e.nome_fantasia, e.cnpj, e.bairro, e.cidade, "  # nosec B608
                           "s.nome AS situacao_resultante " + a.FROM + "WHERE " + a.w + " ORDER BY i.data_interacao, i.id LIMIT :lim"), {**a.p, "lim": lim + 1}).mappings().all()
    crit = [("Período", f"{inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')} (horário de Recife)")]
    if tipo:
        crit.append(("Tipo de atividade", pdf.TIPOS.get(tipo, tipo)))
    if usuario_id:
        crit.append(("Responsável", db.execute(text("SELECT username FROM users WHERE id = :i"), {"i": usuario_id}).scalar() or f"#{usuario_id}"))
    if situacao_id:
        crit.append(("Situação atual da empresa", _nome(db, "situacoes", situacao_id) or f"#{situacao_id}"))
    if cidade and cidade.strip():
        crit.append(("Cidade", cidade.strip()))
    conteudo = pdf.gerar_atividades(titulo=f"Relatório de atividades — {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}", criterios=crit, resumo=resumo,
                                    atividades=[dict(r) for r in rows[:lim]], usuario=user.username, agora=_agora(), truncado=len(rows) > lim)
    audit.record(db, request, "relatorio_pdf_atividades", user_id=user.user_id, username=user.username, details={"atividades": resumo["total"], "inicio": str(inicio), "fim": str(fim)})
    db.commit()
    return _pdf_resposta(conteudo, "relatorio-atividades")
