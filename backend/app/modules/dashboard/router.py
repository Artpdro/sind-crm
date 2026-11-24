"""Painel inicial: totais e distribuições calculados no banco (somente leitura; nunca geocodifica)."""
from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app.db import get_db
from app.security import sessions

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
ATIVAS = "e.deleted_at IS NULL"


@router.get("")
def painel(user=Depends(sessions.current_user), db: Connection = Depends(get_db)):
    q = lambda sql, **p: db.execute(text(sql), p)  # noqa: E731  (SQL fixo; valores como parâmetros)
    total, com_loc, revisao = q(f"SELECT count(*), count(e.latitude), coalesce(sum(e.precisa_revisao), 0) FROM empresas e WHERE {ATIVAS}").one()  # nosec B608
    geo = dict(q(f"SELECT e.geocode_status, count(*) FROM empresas e WHERE {ATIVAS} GROUP BY 1").all())  # nosec B608
    prec = dict(q(f"SELECT coalesce(e.geocode_precisao, 'sem'), count(*) FROM empresas e WHERE {ATIVAS} AND e.latitude IS NOT NULL GROUP BY 1").all())  # nosec B608

    def dist(sql: str, limite: int | None = None):
        rows = [dict(r) for r in q(sql).mappings()]
        if limite and len(rows) > limite:
            resto = sum(r["total"] for r in rows[limite:])
            rows = rows[:limite] + [{"id": None, "nome": "Outras", "cor": None, "total": resto}]
        return rows

    por_cidade = dist(f"SELECT NULL AS id, coalesce(e.cidade, 'Sem cidade') AS nome, NULL AS cor, count(*) AS total FROM empresas e WHERE {ATIVAS} GROUP BY e.cidade ORDER BY 4 DESC, 2", 10)  # nosec B608
    por_situacao = dist(f"SELECT s.id, coalesce(s.nome, 'Sem situação') AS nome, s.cor, count(*) AS total FROM empresas e LEFT JOIN situacoes s ON s.id = e.situacao_id WHERE {ATIVAS} GROUP BY s.id ORDER BY 4 DESC, 2")  # nosec B608
    por_porte = dist(f"SELECT p.id, coalesce(p.nome, 'Sem porte') AS nome, NULL AS cor, count(*) AS total FROM empresas e LEFT JOIN portes p ON p.id = e.porte_id WHERE {ATIVAS} GROUP BY p.id ORDER BY 4 DESC, 2")  # nosec B608
    por_tipo = dist(f"SELECT t.id, coalesce(t.nome, 'Sem tipo') AS nome, NULL AS cor, count(*) AS total FROM empresas e LEFT JOIN tipos_empresa t ON t.id = e.tipo_empresa_id WHERE {ATIVAS} GROUP BY t.id ORDER BY 4 DESC, 2")  # nosec B608
    por_categoria = dist(f"SELECT c.id, c.nome, NULL AS cor, count(*) AS total FROM empresa_categorias ec JOIN categorias c ON c.id = ec.categoria_id JOIN empresas e ON e.id = ec.empresa_id WHERE {ATIVAS} GROUP BY c.id ORDER BY 4 DESC, 2", 10)  # nosec B608
    recentes = [dict(r) for r in q(f"SELECT e.id, e.nome, e.cidade, e.created_at, s.nome AS situacao_nome FROM empresas e LEFT JOIN situacoes s ON s.id = e.situacao_id WHERE {ATIVAS} ORDER BY e.created_at DESC, e.id DESC LIMIT 8").mappings()]  # nosec B608
    interacoes = [dict(r) | {"descricao": r["descricao"][:140]} for r in q(
        "SELECT i.id, i.empresa_id, e.nome AS empresa, i.tipo, i.descricao, i.data_interacao, u.username AS usuario FROM interacoes i "
        f"JOIN empresas e ON e.id = i.empresa_id JOIN users u ON u.id = i.usuario_id WHERE {ATIVAS} ORDER BY i.data_interacao DESC, i.id DESC LIMIT 8").mappings()]  # nosec B608
    n30 = q("SELECT count(*) FROM interacoes WHERE data_interacao >= datetime('now', '-30 days')").scalar_one()
    db.commit()
    return {"total": total, "com_localizacao": com_loc, "sem_localizacao": total - com_loc, "para_revisao": revisao,
            "geocode": {k: geo.get(k, 0) for k in ("localizado", "pendente", "nao_encontrado", "revisao")}, "precisao": prec,
            "interacoes_30_dias": n30, "por_cidade": por_cidade, "por_situacao": por_situacao, "por_porte": por_porte,
            "por_tipo": por_tipo, "por_categoria": por_categoria, "empresas_recentes": recentes, "interacoes_recentes": interacoes}
