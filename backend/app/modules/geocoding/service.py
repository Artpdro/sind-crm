import json
import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, text

from app import timeutil
from app.config import get_settings
from app.modules.companies.normalizers import numero_base
from app.modules.geocoding import cnefe
from app.modules.geocoding.client import GeocoderIndisponivel, Nominatim
from app.modules.geocoding.enderecos import (MUNICIPIOS, ORDINAIS, PREFIXOS, cidades_alternativas, expandir_rua, limpar_rua,  # noqa: F401
                                              rua_e_lugar, variantes_rua)
from app.modules.importacao.rules import UFS
from app.textutil import sem_acento

BRASIL = (-34.0, 6.0, -74.0, -28.0)  # sul, norte, oeste, leste (caixa do Brasil)
CACHE_DIAS = 180      # resultado encontrado
CACHE_NEG_DIAS = 7    # 'não encontrado' expira logo: o mapa do OSM melhora e o usuário pode corrigir o endereço
ESTADO_SERVICO = {"ultimo_erro": None, "ultimo_erro_em": None, "ultimo_ok_em": None, "consultas": 0}  # para o painel de diagnóstico
# Aprendizado: se a busca "com número" quase nunca acha, ela só gasta tempo (1 s por consulta). Conta tentativas/acertos desde que o processo iniciou.
NUMERO = {"tentativas": 0, "acertos": 0, "puladas": 0}
NUMERO_MIN_AMOSTRA, NUMERO_TAXA_MIN, NUMERO_SONDA = 40, 0.05, 25


def resetar_estatisticas() -> None:
    NUMERO.update(tentativas=0, acertos=0, puladas=0)
    ESTADO_SERVICO.update(ultimo_erro=None, ultimo_erro_em=None, ultimo_ok_em=None, consultas=0)


def _pular_busca_com_numero() -> bool:
    if not get_settings().geocoder_pular_numero_raro or NUMERO["tentativas"] < NUMERO_MIN_AMOSTRA:
        return False
    if NUMERO["acertos"] / NUMERO["tentativas"] >= NUMERO_TAXA_MIN:
        return False
    NUMERO["puladas"] += 1
    return NUMERO["puladas"] % NUMERO_SONDA != 0  # a cada 25 puladas, tenta de verdade (sonda): se o mapa melhorar, o sistema percebe


def no_brasil(lat: float, lon: float) -> bool:
    return BRASIL[0] <= lat <= BRASIL[1] and BRASIL[2] <= lon <= BRASIL[3]


@dataclass
class Consulta:
    campos: dict[str, str]
    precisao_esperada: str  # endereco | rua | cep | bairro

    @property
    def chave(self) -> str:
        return json.dumps(sorted(self.campos.items()), ensure_ascii=False)


def montar_consultas(e: dict) -> list[Consulta]:
    """Do mais preciso para o menos preciso; para na primeira que der resultado.
    número+rua → só a rua → busca livre (mais tolerante a grafias) → CEP → bairro (aproximada)."""
    estado = UFS.get((e.get("estado") or "").upper(), "")
    cidade_orig = " ".join((e.get("cidade") or "").split())
    out: list[Consulta] = []

    def add(campos: dict, prec: str):
        out.append(Consulta({**{k: v for k, v in campos.items() if v}, "limit": "5"}, prec))

    if e.get("rua") and cidade_orig:
        rua, emb = limpar_rua(e["rua"])
        if rua and not rua_e_lugar(rua, cidade_orig):
            nb = e.get("numero_base") if e.get("numero_base") is not None else emb
            for cidade in cidades_alternativas(cidade_orig):
                base = {"city": cidade, "state": estado, "country": "Brasil"}
                for r in variantes_rua(rua):
                    if nb is not None:
                        add({**base, "street": f"{nb} {r}"}, "endereco")
                    add({**base, "street": r}, "rua")
                    add({"q": ", ".join(x for x in (r, e.get("bairro"), cidade, estado, "Brasil") if x)}, "rua")
    cep = e.get("cep") or ""
    if len(cep) == 8:
        add({"postalcode": f"{cep[:5]}-{cep[5:]}", "country": "Brasil", "city": cidade_orig}, "cep")
    if e.get("bairro") and cidade_orig:
        add({"q": ", ".join(x for x in (e["bairro"], cidade_orig, estado, "Brasil") if x)}, "bairro")
    vistos, unicas = set(), []
    for c in out:
        if c.chave not in vistos:
            vistos.add(c.chave)
            unicas.append(c)
    return unicas


def endereco_usado(e: dict) -> str:
    return " | ".join(str(e.get(k) or "") for k in ("rua", "numero", "bairro", "cidade", "estado", "cep"))[:300]


def _cidade_confere(esperada: str | None, addr: dict, uf: str | None) -> bool:
    if uf and addr.get("ISO3166-2-lvl4") and not str(addr["ISO3166-2-lvl4"]).upper().endswith("-" + uf.upper()):
        return False
    if not esperada:
        return True
    ex = sem_acento(esperada)
    cands = [sem_acento(addr.get(k)) for k in ("city", "town", "municipality", "village", "county", "city_district") if addr.get(k)]
    return not cands or any(ex == c or ex in c or c in ex for c in cands)


AREA = ("suburb", "neighbourhood", "quarter", "city_district", "borough", "village", "hamlet", "residential")


def interpretar(item: dict, consulta: Consulta, e: dict) -> dict | None:
    """Resposta do Nominatim -> {lat, lon, precisao, confere}; None se inútil (cidade/região, fora do Brasil...)."""
    try:
        lat, lon = float(item["lat"]), float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if lat != lat or lon != lon or not no_brasil(lat, lon):
        return None
    addr = item.get("address") or {}
    rank = int(item.get("place_rank") or 0)
    tipo = item.get("addresstype")
    if addr.get("house_number") or rank >= 30:
        prec = "endereco"
    elif tipo == "postcode" or (consulta.precisao_esperada == "cep" and rank < 26):
        prec = "cep"
    elif tipo == "road" or 26 <= rank < 30:
        prec = "rua"
    elif consulta.precisao_esperada == "bairro" and tipo in AREA:
        prec = "bairro"  # só aceito como último recurso, e sempre marcado para revisão
    else:
        return None  # centro de cidade/região enganaria o usuário
    return {"lat": lat, "lon": lon, "precisao": prec, "confere": _cidade_confere(e.get("cidade"), addr, e.get("estado"))}


def escolher(itens: list[dict], consulta: Consulta, e: dict) -> dict | None:
    """Entre os candidatos devolvidos, prefere o que está na cidade certa e é mais preciso."""
    validos = [r for r in (interpretar(i, consulta, e) for i in itens) if r]
    if not validos:
        return None
    ordem = {"endereco": 0, "rua": 1, "cep": 2, "bairro": 3}
    return min(validos, key=lambda r: (not r["confere"], ordem[r["precisao"]]))


def classificar(achado: dict, e: dict) -> tuple[str, str | None]:
    """(status, observação). 'Localizado' = achado na cidade certa, com número OU pela rua (o OSM quase nunca tem o número no Brasil)."""
    prec = achado["precisao"]
    if not achado["confere"]:
        return "revisao", "A cidade/estado devolvidos diferem do cadastro: confira a localização."
    if prec == "endereco":
        return "localizado", None
    if prec == "rua":
        if re.match(r"(?i)^(rodovia|rod\b|estrada|est\b)", (e.get("rua") or "").strip()):
            return "revisao", "Rodovia/estrada: ponto aproximado ao longo da via."
        return "localizado", "Aproximada pela rua (este número não existe no OpenStreetMap)."
    if prec == "cep":
        return "revisao", "Aproximada pelo CEP: confira."
    return "revisao", "Aproximada pelo bairro: confira."


# ───────── cache e persistência ─────────
def _cache_get(db: Connection, chave: str):
    r = db.execute(text("SELECT encontrado, resultado FROM geocode_cache WHERE chave = :c AND "
                        "criado_em > CASE WHEN encontrado = 1 THEN :pos ELSE :neg END"),
                   {"c": chave, "pos": timeutil.now(days=-CACHE_DIAS), "neg": timeutil.now(days=-CACHE_NEG_DIAS)}).first()
    return None if r is None else (json.loads(r[1]) if r[0] else False)


def _cache_put(db: Connection, chave: str, resultado: dict | None) -> None:
    db.execute(text("INSERT OR REPLACE INTO geocode_cache (chave, encontrado, resultado, criado_em) VALUES (:c, :f, :r, :now)"),
               {"c": chave, "f": int(resultado is not None), "r": json.dumps(resultado) if resultado else None, "now": timeutil.now()})


def esquecer_negativos(db: Connection, e: dict | None) -> None:
    """Pedido de nova tentativa: apaga do cache as respostas 'não encontrado' deste endereço para consultar o serviço de novo."""
    if not e:
        return
    for c in montar_consultas(e):
        db.execute(text("DELETE FROM geocode_cache WHERE chave = :c AND encontrado = 0"), {"c": c.chave})
    db.commit()


def _buscar(db: Connection | None, geo: Nominatim, c: Consulta, e: dict) -> dict | None:
    """Cache primeiro (se houver banco). NUNCA mantém transação aberta durante a chamada de rede."""
    if db is not None:
        hit = _cache_get(db, c.chave)
        db.commit()
        if hit is not None:
            return hit or None
    try:
        resp = geo.buscar(c.campos)  # pode levantar GeocoderIndisponivel (nada é gravado nesse caso)
    except GeocoderIndisponivel as ex:
        ESTADO_SERVICO.update(ultimo_erro=str(ex), ultimo_erro_em=timeutil.now())
        raise
    ESTADO_SERVICO.update(ultimo_ok_em=timeutil.now(), consultas=ESTADO_SERVICO["consultas"] + 1)
    r = escolher(resp.itens, c, e)
    if c.precisao_esperada == "endereco":
        NUMERO["tentativas"] += 1
        NUMERO["acertos"] += int(bool(r and r["precisao"] == "endereco"))
    if db is not None:
        _cache_put(db, c.chave, r)
        db.commit()
    return r


def buscar_melhor(db: Connection | None, geo: Nominatim, e: dict) -> tuple[dict | None, list[dict]]:
    """Percorre as consultas até achar. Retorna (resultado, tentativas) — as tentativas servem ao diagnóstico."""
    tentativas, achado = [], None
    for c in montar_consultas(e):
        if db is not None and c.precisao_esperada == "endereco" and _cache_get(db, c.chave) is None and _pular_busca_com_numero():
            tentativas.append({"nivel": "endereco", "busca": {k: v for k, v in c.campos.items() if k != "limit"}, "achou": False, "pulada": True})
            continue
        achado = _buscar(db, geo, c, e)
        tentativas.append({"nivel": c.precisao_esperada, "busca": {k: v for k, v in c.campos.items() if k != "limit"}, "achou": bool(achado)})
        if achado:
            break
    return achado, tentativas


COLS = "id, rua, numero, numero_base, bairro, cidade, estado, cep, latitude, longitude, geocode_status, geocode_desatualizada, geocode_precisao, version"


def carregar(db: Connection, empresa_id: int) -> dict | None:
    r = db.execute(text(f"SELECT {COLS} FROM empresas WHERE id = :i AND deleted_at IS NULL"), {"i": empresa_id}).mappings().first()  # nosec B608
    db.commit()
    return dict(r) if r else None


def motivo_nao_encontrada(e: dict, tentativas: list[dict]) -> str:
    if e.get("rua") and rua_e_lugar(limpar_rua(e["rua"])[0], e.get("cidade")) and not e.get("bairro"):
        return "O cadastro informa só o município (sem rua nem bairro): corrija o endereço ou informe a localização manualmente."
    if not tentativas:
        return "Endereço sem rua, bairro ou CEP utilizáveis."
    return f"O serviço não encontrou este endereço ({len(tentativas)} tentativa(s))."


def processar_empresa(db: Connection, geo: Nominatim, empresa_id: int) -> str:
    """Geocodifica UMA empresa e grava o resultado. Retorna: localizado | revisao | nao_encontrado | pulada | indisponivel."""
    e = carregar(db, empresa_id)
    if not e:
        return "pulada"
    usado = endereco_usado(e)
    achado, tentativas = cnefe.localizar(e), []  # 1ª fonte: IBGE/CNEFE (exata por número, sem internet)
    if achado is None:
        try:
            achado, tentativas = buscar_melhor(db, geo, e)  # 2ª fonte: OpenStreetMap
        except GeocoderIndisponivel:
            return "indisponivel"
        if achado:
            achado["fonte"] = "osm"
    tinha_coord = e["latitude"] is not None
    atual = carregar(db, empresa_id)  # o endereço pode ter sido editado durante a consulta
    if not atual or endereco_usado(atual) != usado:
        return "pulada"
    if achado:
        status, obs = (achado["status"], achado["obs"]) if achado.get("fonte") == "cnefe" else classificar(achado, e)
        db.execute(text("UPDATE empresas SET latitude=:la, longitude=:lo, geocode_status=:s, geocode_endereco_usado=:u, geocoded_at=:now, "
                        "geocode_desatualizada=0, geocode_precisao=:p, geocode_fonte=:f, geocode_erro=:m, geocode_tentativas=geocode_tentativas+1 WHERE id=:i"),
                   {"la": achado["lat"], "lo": achado["lon"], "s": status, "u": usado, "now": timeutil.now(), "p": achado["precisao"],
                    "f": achado.get("fonte", "osm"), "m": obs, "i": empresa_id})
        db.commit()
        return status
    # Não achou: se já havia coordenada, ela é PRESERVADA (só vira "revisão"); senão fica "não encontrado".
    motivo = motivo_nao_encontrada(e, [t for t in tentativas if not t.get("pulada")])
    db.execute(text("UPDATE empresas SET geocode_status=:s, geocode_endereco_usado=:u, geocode_erro=:m, geocode_tentativas=geocode_tentativas+1 WHERE id=:i"),
               {"s": "revisao" if tinha_coord else "nao_encontrado", "u": usado, "m": motivo, "i": empresa_id})
    db.commit()
    return "revisao" if tinha_coord else "nao_encontrado"


# ───────── trava de execução única ─────────
def adquirir(db: Connection, holder: str, segundos: int = 120) -> bool:
    n = db.execute(text("UPDATE geocode_lease SET holder=:h, expires_at=:e WHERE id=1 AND (holder IS NULL OR holder=:h OR expires_at < :now)"),
                   {"h": holder, "e": timeutil.now(seconds=segundos), "now": timeutil.now()}).rowcount
    db.commit()
    return n == 1


def liberar(db: Connection, holder: str) -> None:
    db.execute(text("UPDATE geocode_lease SET holder=NULL, expires_at=NULL WHERE id=1 AND holder=:h"), {"h": holder})
    db.commit()


def pendentes(db: Connection, limite: int, apos_id: int = 0, retentar_falhas: bool = False) -> list[int]:
    rows = db.execute(text(
        "SELECT id FROM empresas WHERE deleted_at IS NULL AND id > :a AND (geocode_status = 'pendente' "
        "OR (:r = 1 AND geocode_status = 'nao_encontrado')) ORDER BY id LIMIT :l"),
        {"a": apos_id, "r": int(retentar_falhas), "l": limite}).scalars().all()
    db.commit()
    return list(rows)


def processar_lote(db: Connection, geo: Nominatim, limite: int, holder: str | None = None, retentar_falhas: bool = False,
                   max_erros_seguidos: int = 3, apos_id: int = 0, ao_progredir=None) -> dict:
    holder = holder or f"lote-{uuid.uuid4().hex[:8]}"
    if not adquirir(db, holder):
        return {"ocupado": True}
    cont = {"localizado": 0, "revisao": 0, "nao_encontrado": 0, "pulada": 0, "indisponivel": 0}
    seguidos, ultimo = 0, apos_id
    try:
        for eid in pendentes(db, limite, apos_id, retentar_falhas):
            adquirir(db, holder)  # renova a trava
            if retentar_falhas:
                esquecer_negativos(db, carregar(db, eid))
            r = processar_empresa(db, geo, eid)
            cont[r] += 1
            ultimo = eid
            seguidos = seguidos + 1 if r == "indisponivel" else 0
            if ao_progredir:
                ao_progredir(eid, r)
            if seguidos >= max_erros_seguidos:  # serviço fora do ar / bloqueou: para em vez de insistir
                cont["interrompido"] = True
                break
    finally:
        liberar(db, holder)
    cont["ultimo_id"] = ultimo
    return cont


def resumo(db: Connection) -> dict:
    por = dict(db.execute(text("SELECT geocode_status, count(*) FROM empresas WHERE deleted_at IS NULL GROUP BY 1")).all())
    desat = db.execute(text("SELECT count(*) FROM empresas WHERE deleted_at IS NULL AND geocode_desatualizada = 1")).scalar_one()
    s = get_settings()
    from app.modules.geocoding.auto import auto
    pend = por.get("pendente", 0)
    return {"automatica": auto.ativo, "estimativa_min": math.ceil(pend * (s.geocoder_min_interval + 0.3) / 60), "localizado": por.get("localizado", 0), "pendente": por.get("pendente", 0), "nao_encontrado": por.get("nao_encontrado", 0),
            "revisao": por.get("revisao", 0), "desatualizadas": desat, "total": sum(por.values()),
            "contato_configurado": bool(s.geocoder_email), "servico": dict(ESTADO_SERVICO), "cnefe": cnefe.estado()}


def criar_geocoder() -> Nominatim:
    s = get_settings()
    return Nominatim(s.geocoder_url, s.geocoder_email, s.geocoder_min_interval, permitir_local=s.geocoder_allow_local)


# ───────── reprocessar, testar conexão, diagnóstico ─────────
ESCOPOS = {
    "nao_encontradas": "geocode_status = 'nao_encontrado'",
    "revisao": "geocode_status = 'revisao' AND COALESCE(geocode_precisao, '') <> 'manual'",
    "todas": "geocode_status IN ('localizado', 'revisao', 'nao_encontrado') AND COALESCE(geocode_precisao, '') <> 'manual'",
}


def reprocessar(db: Connection, escopo: str) -> int:
    """Devolve empresas à fila para serem tentadas de novo com o motor atual. As coordenadas existentes são preservadas
    até haver outras válidas; correções MANUAIS nunca são refeitas."""
    where = ESCOPOS[escopo]  # chave validada pelo chamador (lista fixa)
    n = db.execute(text("UPDATE empresas SET geocode_status = 'pendente', geocode_tentativas = 0, geocode_erro = NULL, "  # nosec B608
                        "geocode_desatualizada = CASE WHEN latitude IS NOT NULL THEN 1 ELSE geocode_desatualizada END "
                        "WHERE deleted_at IS NULL AND " + where)).rowcount
    db.execute(text("DELETE FROM geocode_cache WHERE encontrado = 0"))
    db.commit()
    return n


def testar_conexao(geo: Nominatim) -> dict:
    """Uma consulta conhecida, para provar se o serviço responde (e se não está bloqueando este computador)."""
    import time
    c = Consulta({"q": "Avenida Agamenon Magalhães, Recife, Pernambuco, Brasil", "limit": "1"}, "rua")
    t = time.time()
    try:
        resp = geo.buscar(c.campos)
    except GeocoderIndisponivel as ex:
        ESTADO_SERVICO.update(ultimo_erro=str(ex), ultimo_erro_em=timeutil.now())
        return {"ok": False, "mensagem": str(ex), "ms": round((time.time() - t) * 1000)}
    ESTADO_SERVICO.update(ultimo_ok_em=timeutil.now())
    ms = round((time.time() - t) * 1000)
    if not resp.itens:
        return {"ok": False, "mensagem": "O serviço respondeu, mas sem resultados para uma consulta que deveria achar.", "ms": ms}
    return {"ok": True, "mensagem": "O serviço de geocodificação respondeu normalmente.", "ms": ms, "resultados": len(resp.itens)}


def diagnostico(db: Connection) -> dict:
    base = "FROM empresas WHERE deleted_at IS NULL"
    por_fonte = dict(db.execute(text("SELECT COALESCE(geocode_fonte, 'sem coordenada'), count(*) FROM empresas WHERE deleted_at IS NULL AND latitude IS NOT NULL GROUP BY 1")).all())
    por_prec = dict(db.execute(text("SELECT COALESCE(geocode_precisao, 'sem coordenada'), count(*) " + base + " AND latitude IS NOT NULL GROUP BY 1")).all())  # nosec B608
    motivos = [{"status": a, "motivo": b, "quantidade": c} for a, b, c in db.execute(text(
        "SELECT geocode_status, geocode_erro, count(*) " + base + " AND geocode_erro IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12"))]  # nosec B608
    sem_dado = db.execute(text("SELECT count(*) " + base + " AND (rua IS NULL OR cidade IS NULL)")).scalar_one()  # nosec B608
    exemplos = [dict(r) for r in db.execute(text(
        "SELECT id, rua, numero, bairro, cidade, cep, geocode_erro AS motivo " + base + " AND geocode_status = 'nao_encontrado' ORDER BY id LIMIT 15")).mappings()]  # nosec B608
    db.commit()
    return {"por_fonte": por_fonte, "por_precisao": por_prec, "motivos": motivos, "sem_rua_ou_cidade": sem_dado, "exemplos_nao_encontradas": exemplos,
            "resumo": resumo(db)}
