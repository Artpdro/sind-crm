"""Localização EXATA por número, usando o CNEFE do IBGE (Censo 2022): cadastro oficial de endereços com rua, número e coordenada.

O OpenStreetMap quase não tem número de casa no Brasil; o CNEFE tem. Este módulo guarda os endereços numa base SQLite própria
(`data/cnefe.db`, só leitura durante o uso) e responde, SEM internet:
  1. número exato na rua            -> precisão 'endereco'
  2. número ausente na base          -> estimado entre os vizinhos do mesmo lado da rua -> 'interpolado'
  3. só a rua                        -> 'rua' (ponto médio da rua)
Se a rua não for achada, devolve None e o motor cai no OpenStreetMap.
"""
import codecs
import csv
import difflib
import io
import re
import sqlite3
import statistics
import threading
import zipfile
from pathlib import Path

from app import timeutil
from app.config import get_settings
from app.modules.geocoding.enderecos import cidades_alternativas, limpar_rua, rua_e_lugar
from app.textutil import distancia_m, sem_acento

BRASIL = (-34.0, 6.0, -74.0, -28.0)
MUNICIPIOS_CONHECIDOS = {2610707: "Paulista", 2609600: "Olinda", 2606804: "Igarassu", 2600054: "Abreu e Lima",
                         2607604: "Ilha de Itamaracá", 2607752: "Itapissuma", 2611606: "Recife", 2603454: "Camaragibe"}
TIPOS = {"rua", "avenida", "travessa", "praca", "alameda", "rodovia", "estrada", "largo", "vila", "beco", "ladeira", "caminho",
         "passagem", "viela", "via", "pateo", "parque", "quadra", "loteamento", "conjunto", "residencial"}
ABREV = {"r": "rua", "av": "avenida", "tv": "travessa", "trav": "travessa", "pc": "praca", "pca": "praca", "al": "alameda",
         "rod": "rodovia", "est": "estrada", "estr": "estrada", "lgo": "largo", "vl": "vila", "bc": "beco",
         "dr": "doutor", "dra": "doutora", "prof": "professor", "profa": "professora", "cel": "coronel", "maj": "major",
         "cap": "capitao", "ten": "tenente", "gen": "general", "eng": "engenheiro", "mons": "monsenhor", "pres": "presidente",
         "gov": "governador", "des": "desembargador", "alm": "almirante", "mal": "marechal", "sgt": "sargento", "cmte": "comandante"}
TITULOS = {"doutor", "doutora", "professor", "professora", "coronel", "major", "capitao", "tenente", "general", "engenheiro",
           "monsenhor", "padre", "presidente", "governador", "desembargador", "almirante", "marechal", "sargento", "comandante",
           "senador", "deputado", "vereador", "prefeito", "bispo", "frei", "dom", "juiz", "comendador", "cabo"}
LIGACAO = {"de", "da", "do", "das", "dos", "e"}
ORD = {"1": "primeira", "2": "segunda", "3": "terceira", "4": "quarta", "5": "quinta", "6": "sexta", "7": "setima", "8": "oitava", "9": "nona"}

COLUNAS_OBRIGATORIAS = ["COD_MUNICIPIO", "NOM_SEGLOGR", "NUM_ENDERECO", "LATITUDE", "LONGITUDE"]
LIMITE_CSV_BYTES = 4 * 1024**3
MAX_DIST_INTERPOLAR_M = 600
MAX_GAP_NUMEROS = 300
MAX_VIZINHO_DIFF = 20
MAX_DISPERSAO_M = 150

_trava = threading.Lock()
_con: dict[str, sqlite3.Connection] = {}
_ruas_cache: dict[tuple[str, int, int], list[str]] = {}


# ───────── normalização de nomes de rua ─────────
def _tokens(texto: str) -> list[str]:
    t = sem_acento(texto) or ""
    t = re.sub(r"\b([1-9])\s*[ªºao]\b", lambda m: ORD[m.group(1)], t)  # '1ª Travessa' -> 'primeira travessa'
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return [ABREV.get(x, x) for x in t.split()]


def chaves_rua(rua: str, com_tipo: bool = True) -> tuple[str, str]:
    """(completa, curta). completa = com títulos ('doutor luis ignacio andrade lima'); curta = sem títulos nem ligações."""
    toks = _tokens(rua)
    if com_tipo and toks and toks[0] in TIPOS and len(toks) > 1:
        toks = toks[1:]
    toks = [x for x in toks if x not in LIGACAO]
    curta = [x for x in toks if x not in TITULOS]
    return " ".join(toks), " ".join(curta or toks)


def _cod_para_nome(nome: str) -> str:
    return sem_acento(nome) or ""


# ───────── base de dados ─────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS municipios (cod INTEGER PRIMARY KEY, nome TEXT NOT NULL, nome_norm TEXT NOT NULL, pontos INTEGER NOT NULL, importado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pontos (mun INTEGER NOT NULL, chave TEXT NOT NULL, completa TEXT NOT NULL, num INTEGER NOT NULL,
                                   lat REAL NOT NULL, lon REAL NOT NULL, cep TEXT);
CREATE INDEX IF NOT EXISTS pontos_idx ON pontos (mun, chave, num);
"""


def caminho_base() -> Path:
    return Path(get_settings().cnefe_db)


def _abrir(somente_leitura: bool = True) -> sqlite3.Connection | None:
    p = caminho_base()
    if somente_leitura:
        if not p.exists():
            return None
        chave = str(p.resolve())
        with _trava:
            c = _con.get(chave)
            if c is None:
                c = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True, check_same_thread=False)
                _con[chave] = c
            return c
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.executescript(SCHEMA)
    return c


def limpar_cache() -> None:
    with _trava:
        for c in _con.values():
            c.close()
        _con.clear()
        _ruas_cache.clear()


def disponivel() -> bool:
    return get_settings().cnefe_ativo and caminho_base().exists()


def estado() -> dict:
    """Para o painel: a base está importada? quais municípios? quantos endereços?"""
    s = get_settings()
    c = _abrir() if s.cnefe_ativo else None
    if c is None:
        return {"ativo": False, "importado": caminho_base().exists(), "municipios": [], "pontos": 0}
    try:
        with _trava:
            ms = [{"cod": r[0], "nome": r[1], "pontos": r[2]} for r in c.execute("SELECT cod, nome, pontos FROM municipios ORDER BY nome")]
    except sqlite3.Error:
        ms = []
    return {"ativo": bool(ms), "importado": True, "municipios": ms, "pontos": sum(m["pontos"] for m in ms)}


# ───────── importação ─────────
class CnefeInvalido(Exception):
    pass


def _abrir_csv(origem: Path):
    """Devolve (fluxo_de_texto, fechar). Aceita .zip (lê o .csv de dentro, sem extrair para o disco) ou .csv."""
    if origem.suffix.lower() == ".zip":
        z = zipfile.ZipFile(origem)
        info = [i for i in z.infolist() if i.filename.lower().endswith(".csv") and not i.filename.startswith(("/", "..")) and ".." not in i.filename]
        if not info:
            z.close()
            raise CnefeInvalido("O .zip não contém um arquivo .csv.")
        i = max(info, key=lambda x: x.file_size)
        if i.file_size > LIMITE_CSV_BYTES or (i.compress_size and i.file_size / i.compress_size > 200):
            z.close()
            raise CnefeInvalido("O arquivo dentro do .zip tem tamanho/compressão suspeitos.")
        return z.open(i), z.close
    f = open(origem, "rb")
    return f, f.close


class _Encadeado(io.RawIOBase):
    """Devolve primeiro os bytes já lidos (amostra) e depois o resto do fluxo."""

    def __init__(self, inicio: bytes, resto):
        self._ini, self._resto = inicio, resto

    def readable(self):
        return True

    def readinto(self, b):
        if self._ini:
            n = min(len(b), len(self._ini))
            b[:n], self._ini = self._ini[:n], self._ini[n:]
            return n
        dados = self._resto.read(len(b))
        b[:len(dados)] = dados
        return len(dados)


def _leitor(binario):
    """Detecta separador pelo cabeçalho e a codificação (UTF-8 ou Latin-1) por uma amostra do conteúdo."""
    cab = binario.readline()
    amostra = binario.read(1 << 20)
    try:
        codecs.getincrementaldecoder("utf-8")().decode(cab + amostra, final=False)
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        enc = "latin-1"
    linha = cab.decode(enc, errors="replace")
    sep = ";" if linha.count(";") >= linha.count(",") else ","
    texto = io.TextIOWrapper(io.BufferedReader(_Encadeado(amostra, binario)), encoding=enc, errors="replace", newline="")
    cols = [c.strip().strip('"').upper() for c in next(csv.reader([linha], delimiter=sep))]
    return cols, csv.reader(texto, delimiter=sep), enc


def _num(v: str) -> float | None:
    try:
        return float(v.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


def importar(origem: str | Path, nome_municipio: str | None = None, saida=print) -> dict:
    """Lê um .zip/.csv do CNEFE de UM município e grava em data/cnefe.db (substitui o que havia desse município)."""
    origem = Path(origem)
    if not origem.is_file():
        raise CnefeInvalido(f"Arquivo não encontrado: {origem}")
    binario, fechar = _abrir_csv(origem)
    try:
        cols, linhas, _ = _leitor(binario)
        faltam = [c for c in COLUNAS_OBRIGATORIAS if c not in cols]
        if faltam:
            raise CnefeInvalido(f"Este arquivo não parece ser o CNEFE: faltam as colunas {', '.join(faltam)}. Cabeçalho lido: {', '.join(cols[:12])}…")
        ix = {c: cols.index(c) for c in cols}
        g = lambda r, c: r[ix[c]].strip() if c in ix and ix[c] < len(r) else ""  # noqa: E731
        con = _abrir(somente_leitura=False)
        lote, total, lidas, descartadas, cod = [], 0, 0, 0, None
        con.execute("BEGIN")
        try:
            for r in linhas:
                lidas += 1
                try:
                    mun = int(g(r, "COD_MUNICIPIO"))
                except ValueError:
                    descartadas += 1
                    continue
                if cod is None:
                    cod = mun
                    con.execute("DELETE FROM pontos WHERE mun = ?", (cod,))
                if mun != cod:
                    raise CnefeInvalido("O arquivo mistura mais de um município. Importe um município por arquivo.")
                nome_rua = " ".join(x for x in (g(r, "NOM_TITULO_SEGLOGR"), g(r, "NOM_SEGLOGR")) if x)
                digitos = re.sub(r"\D", "", g(r, "NUM_ENDERECO"))
                lat, lon = _num(g(r, "LATITUDE")), _num(g(r, "LONGITUDE"))
                if not nome_rua or not digitos or not (0 < int(digitos) < 100000) or lat is None or lon is None \
                        or not (BRASIL[0] <= lat <= BRASIL[1] and BRASIL[2] <= lon <= BRASIL[3]):
                    descartadas += 1
                    continue
                completa, curta = chaves_rua(nome_rua, com_tipo=False)
                if not curta:
                    descartadas += 1
                    continue
                lote.append((cod, curta, completa, int(digitos), lat, lon, re.sub(r"\D", "", g(r, "CEP")) or None))
                if len(lote) >= 5000:
                    con.executemany("INSERT INTO pontos VALUES (?,?,?,?,?,?,?)", lote)
                    total += len(lote)
                    lote = []
                    if total % 100000 < 5000:
                        saida(f"  {total} endereços…")
            if lote:
                con.executemany("INSERT INTO pontos VALUES (?,?,?,?,?,?,?)", lote)
                total += len(lote)
            if cod is None or total == 0:
                raise CnefeInvalido("Nenhum endereço com rua, número e coordenada foi encontrado no arquivo.")
            nome = nome_municipio or MUNICIPIOS_CONHECIDOS.get(cod)
            if not nome:
                m = re.search(r"\d{6,7}[_ -]+([A-Za-zÀ-ú_ ]+)", origem.stem)
                nome = m.group(1).replace("_", " ").strip().title() if m else None
            if not nome:
                raise CnefeInvalido(f"Município {cod} não reconhecido: informe o nome com --nome.")
            con.execute("INSERT OR REPLACE INTO municipios VALUES (?,?,?,?,?)", (cod, nome, _cod_para_nome(nome), total, timeutil.now()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        limpar_cache()
        return {"cod": cod, "nome": nome, "pontos": total, "lidas": lidas, "descartadas": descartadas}
    finally:
        fechar()


# ───────── consulta ─────────
def _municipio(c: sqlite3.Connection, cidade: str | None) -> int | None:
    if not cidade:
        return None
    nomes = [sem_acento(x) for x in cidades_alternativas(cidade)]
    with _trava:
        for n in nomes:
            r = c.execute("SELECT cod FROM municipios WHERE nome_norm = ?", (n,)).fetchone()
            if r:
                return r[0]
    return None


def _ruas_do_municipio(c: sqlite3.Connection, mun: int) -> list[str]:
    k = (str(caminho_base()), mun, caminho_base().stat().st_mtime_ns)  # muda quando a base é reimportada (mesmo por outro processo)
    if k not in _ruas_cache:
        with _trava:
            _ruas_cache[k] = [r[0] for r in c.execute("SELECT DISTINCT chave FROM pontos WHERE mun = ?", (mun,))]
    return _ruas_cache[k]


def _resolver_chave(c, mun: int, curta: str) -> str | None:
    """Chave exata; senão a única rua que contém (ou está contida em) o nome, ou a única parecida (erro de digitação)."""
    ruas = _ruas_do_municipio(c, mun)
    if curta in ruas:
        return curta
    t = set(curta.split())
    sub = [r for r in ruas if len(t) >= 2 and (t <= set(r.split()) or (len(r.split()) >= 2 and set(r.split()) <= t))]
    if len(sub) == 1:
        return sub[0]
    pr = difflib.get_close_matches(curta, ruas, n=2, cutoff=0.9)
    if len(pr) == 1 or (len(pr) == 2 and difflib.SequenceMatcher(None, curta, pr[0]).ratio() - difflib.SequenceMatcher(None, curta, pr[1]).ratio() > 0.05):
        return pr[0]
    return None


def _mediana(pts):
    return statistics.median(p[2] for p in pts), statistics.median(p[3] for p in pts)


def _dispersao(pts) -> float:
    la, lo = _mediana(pts)
    return max((distancia_m(la, lo, p[2], p[3]) or 0 for p in pts), default=0)


def localizar(e: dict) -> dict | None:
    """Endereço da empresa -> {lat, lon, precisao, confere, fonte, status, obs} ou None (rua não encontrada / base não importada)."""
    if not get_settings().cnefe_ativo:
        return None
    c = _abrir()
    if c is None or not e.get("rua") or not e.get("cidade"):
        return None
    try:
        rua, emb = _limpar(e["rua"])
        if not rua or rua_e_lugar(rua, e.get("cidade")):
            return None
        mun = _municipio(c, e["cidade"])
        if mun is None:
            return None
        completa, curta = chaves_rua(rua)
        chave = _resolver_chave(c, mun, curta)
        if chave is None:
            return None
        with _trava:
            rows = c.execute("SELECT completa, num, lat, lon, cep FROM pontos WHERE mun = ? AND chave = ?", (mun, chave)).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    pts = [(r[1], r[1], r[2], r[3], r[4], r[0]) for r in rows]  # (num, num, lat, lon, cep, completa)
    ambiguo_nome = False
    nomes = {p[5] for p in pts}
    if len(nomes) > 1:  # 'doutor joao silva' e 'joao silva' na mesma cidade
        exata = [p for p in pts if p[5] == completa]
        if exata:
            pts = exata
        else:
            cep = e.get("cep") or ""
            por_cep = [p for p in pts if cep and p[4] == cep]
            if por_cep:
                pts = por_cep
            else:
                ambiguo_nome = True
    nb = e.get("numero_base") if e.get("numero_base") is not None else emb
    r = _por_numero(pts, nb, e.get("cep") or "") if nb else None
    if r is None:
        la, lo = _mediana(pts)
        rodovia = re.match(r"(?i)^(rodovia|rod\b|estrada|est\b)", (e.get("rua") or "").strip())
        longa = _dispersao(pts) > 3000
        r = {"lat": la, "lon": lo, "precisao": "rua",
             "status": "revisao" if (rodovia or longa) else "localizado",
             "obs": ("Rodovia/estrada: ponto aproximado ao longo da via." if rodovia else
                     "Rua muito extensa: ponto aproximado, confira." if longa else
                     "Aproximada pela rua (IBGE): o cadastro não informa número." if not nb else
                     f"Aproximada pela rua (IBGE): o número {nb} não consta na base.")}
    if ambiguo_nome and r["status"] == "localizado":
        r["status"], r["obs"] = "revisao", "Há mais de uma rua com esse nome na cidade: confira."
    return {**r, "confere": True, "fonte": "cnefe"}


def _limpar(rua: str):
    return limpar_rua(rua)


def _por_numero(pts, nb: int, cep: str) -> dict | None:
    exatos = [p for p in pts if p[0] == nb]
    if exatos:
        if cep and any(p[4] == cep for p in exatos) and _dispersao(exatos) > MAX_DISPERSAO_M:
            exatos = [p for p in exatos if p[4] == cep]
        la, lo = _mediana(exatos)
        if _dispersao(exatos) > MAX_DISPERSAO_M:
            return {"lat": la, "lon": lo, "precisao": "endereco", "status": "revisao",
                    "obs": "Há mais de um ponto com este número na rua (IBGE): confira."}
        return {"lat": la, "lon": lo, "precisao": "endereco", "status": "localizado", "obs": None}
    por_num: dict[int, tuple[float, float]] = {}
    for n in {p[0] for p in pts}:
        ps = [p for p in pts if p[0] == n]
        por_num[n] = _mediana(ps)
    for paridade in (nb % 2, None):  # primeiro o mesmo lado da rua; se não houver, qualquer lado
        cand = {n: v for n, v in por_num.items() if paridade is None or n % 2 == paridade}
        abaixo = max((n for n in cand if n < nb), default=None)
        acima = min((n for n in cand if n > nb), default=None)
        if abaixo is not None and acima is not None and acima - abaixo <= MAX_GAP_NUMEROS:
            (la1, lo1), (la2, lo2) = cand[abaixo], cand[acima]
            if (distancia_m(la1, lo1, la2, lo2) or 0) <= MAX_DIST_INTERPOLAR_M:
                f = (nb - abaixo) / (acima - abaixo)
                return {"lat": la1 + (la2 - la1) * f, "lon": lo1 + (lo2 - lo1) * f, "precisao": "interpolado", "status": "localizado",
                        "obs": f"Estimada entre os números {abaixo} e {acima} (IBGE): o número {nb} não consta na base."}
        perto = min(cand, key=lambda n: abs(n - nb), default=None)
        if perto is not None and abs(perto - nb) <= MAX_VIZINHO_DIFF:
            la, lo = cand[perto]
            return {"lat": la, "lon": lo, "precisao": "interpolado", "status": "localizado",
                    "obs": f"Próxima ao número {perto} (IBGE): o número {nb} não consta na base."}
    return None
