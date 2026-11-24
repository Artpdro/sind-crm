"""Funções PURAS de tratamento de endereço (sem banco nem rede): usadas pelo OpenStreetMap e pelo CNEFE."""
import re

from app.textutil import sem_acento

PREFIXOS = {"r": "Rua", "av": "Avenida", "tv": "Travessa", "trav": "Travessa", "pc": "Praça", "pca": "Praça", "pç": "Praça",
            "al": "Alameda", "rod": "Rodovia", "est": "Estrada", "estr": "Estrada", "lgo": "Largo", "vl": "Vila", "bc": "Beco"}
ORDINAIS = {1: "Primeira", 2: "Segunda", 3: "Terceira", 4: "Quarta", 5: "Quinta", 6: "Sexta", 7: "Sétima", 8: "Oitava", 9: "Nona", 10: "Décima"}
# Na planilha real, ~5% das "ruas" são na verdade o nome do município/área (ex.: "Itapissuma", "Área Rural"): isso NÃO é rua.
MUNICIPIOS = {"itapissuma", "ilha de itamaraca", "itamaraca", "abreu e lima", "igarassu", "paulista", "olinda", "recife", "camaragibe",
              "jaboatao dos guararapes", "cabo de santo agostinho", "sao lourenco da mata", "goiana"}


def expandir_rua(rua: str) -> str:
    m = re.match(r"^([A-Za-zÀ-ú]{1,5})\.?\s+(.*)$", rua.strip())
    if m and m.group(1).lower() in PREFIXOS and not rua.lower().startswith(PREFIXOS[m.group(1).lower()].lower()):
        return f"{PREFIXOS[m.group(1).lower()]} {m.group(2)}"
    return rua.strip()


def limpar_rua(rua: str) -> tuple[str, int | None]:
    """'Avenida João Pessoa, 120 AC Itapissuma' -> ('Avenida João Pessoa', 120). Corta lixo depois de vírgula, ' - ' e 's/n'."""
    r = " ".join(str(rua).split())
    cabeca, _, cauda = r.partition(",")
    m = re.match(r"\s*(\d{1,6})\b", cauda)
    emb = int(m.group(1)) if m else None
    r = re.split(r"\s+-\s+", cabeca)[0].strip()
    r = re.sub(r"(?i)\s+s/?n\b.*$", "", r).strip()
    return r, emb


def variantes_rua(rua: str) -> list[str]:
    """Formas equivalentes que o OpenStreetMap pode usar: 'Rodovia BR-101 Norte' -> 'Rodovia BR-101'; '1ª Travessa X' -> 'Primeira Travessa X'."""
    r = expandir_rua(rua)
    out = []
    m = re.match(r"(?i)^(?:rodovia|rod\.?)\s+([A-Za-z]{2})[\s-]?(\d{1,3})\b", r)
    if m:
        out.append(f"Rodovia {m.group(1).upper()}-{m.group(2)}")
    else:
        out.append(r)
        o = re.match(r"^(\d{1,2})\s*[ªº]?\s+(.*)$", r)
        if o and int(o.group(1)) in ORDINAIS:
            out.append(f"{ORDINAIS[int(o.group(1))]} {o.group(2)}")
    return list(dict.fromkeys(out))


def rua_e_lugar(rua: str, cidade: str | None) -> bool:
    sa = sem_acento(rua) or ""
    return sa in MUNICIPIOS or sa == sem_acento(cidade or "") or sa.startswith(("area rural", "zona rural"))


def cidades_alternativas(cidade: str) -> list[str]:
    c = " ".join(cidade.split())
    alts = [c]
    if (sem_acento(c) or "").startswith("ilha de "):
        alts.append(c[8:].strip())  # 'Ilha de Itamaracá' também aparece como 'Itamaracá'
    return alts
