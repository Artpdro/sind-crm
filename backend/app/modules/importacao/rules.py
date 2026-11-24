"""Regras de normalização e validação de UMA linha da planilha.
Nada é inventado: dado inválido NÃO entra no campo; o valor original vai para o motivo de revisão."""
import re
import unicodedata
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email

from app.modules.companies import normalizers as nz

LIMITES = {"nome": 300, "nome_fantasia": 300, "email": 320, "rua": 300, "numero": 60, "complemento": 300,
           "bairro": 150, "cidade": 150}
UFS = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
       "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
       "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
       "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
       "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins"}


def _k(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower().strip()


NOME_PARA_UF = {_k(v): k for k, v in UFS.items()}
CAMPOS_INFO = ("nome_fantasia", "cnpj", "email", "emails_adicionais", "celular", "fone", "rua", "numero",
               "complemento", "bairro", "cidade", "cep", "estado")


def limpar(v: str) -> str:
    v = "".join(c for c in v if c == "\n" or c == "\t" or ord(c) >= 32)
    return " ".join(v.split())


def neutralizar(v: str | None) -> str | None:
    """Injeção de fórmula (Excel/CSV): texto que começa com = + - @ vira texto literal (prefixo apóstrofo)."""
    if v and v[0] in "=+-@\t\r" and (len(v) > 1 or v[0] in "=@"):
        return "'" + v
    return v


def uf_de(texto: str) -> str | None:
    t = texto.strip()
    if len(t) == 2 and t.upper() in UFS:
        return t.upper()
    return NOME_PARA_UF.get(_k(t))


@dataclass
class Linha:
    dados: dict = field(default_factory=dict)
    motivos: list[str] = field(default_factory=list)   # marcam a empresa para REVISÃO
    avisos: list[str] = field(default_factory=list)    # só informativos
    erros: list[str] = field(default_factory=list)     # rejeitam a linha


def _emails(texto: str) -> tuple[list[str], list[str]]:
    ok, ruins = [], []
    for tok in [t for t in re.split(r"[;,\s]+", texto) if t]:
        try:
            ok.append(validate_email(tok, check_deliverability=False).normalized)
        except EmailNotValidError:
            ruins.append(tok)
    return ok, ruins


def processar(raw: dict[str, str]) -> Linha:
    r = Linha()
    d = r.dados
    campos = {k: limpar(v) for k, v in raw.items()}
    for campo, lim in LIMITES.items():
        if len(campos.get(campo, "")) > lim:
            r.erros.append(f"Campo '{campo}' excede {lim} caracteres.")
    if len(campos.get("emails_adicionais", "")) > 2000:
        r.erros.append("Campo 'emails_adicionais' é longo demais.")
    if not campos.get("nome"):
        r.erros.append("Linha sem Nome da Empresa.")
    if r.erros:
        return r

    # CNPJ
    d["cnpj"], d["cnpj_original"] = None, None
    bruto = campos.get("cnpj", "")
    if not bruto:
        r.motivos.append("Sem CNPJ informado.")
    else:
        try:
            d["cnpj"] = nz.normalize_cnpj(bruto)
        except ValueError as e:
            d["cnpj_original"] = bruto[:100]
            r.motivos.append(f"CNPJ informado ('{bruto[:40]}') é inválido ou está incompleto: {e}")

    # Emails (o principal e os adicionais podem vir misturados)
    ok_p, ruins_p = _emails(campos.get("email", ""))
    ok_a, ruins_a = _emails(campos.get("emails_adicionais", ""))
    todos = list(dict.fromkeys(ok_p + ok_a))
    d["email"] = todos[0] if todos else None
    extras = todos[1:]
    if len(extras) > 20:
        r.motivos.append("Mais de 20 emails informados; os excedentes não foram importados.")
        extras = extras[:20]
    d["emails_adicionais"] = extras
    for ruim in (ruins_p + ruins_a)[:5]:
        r.motivos.append(f"Email inválido ignorado: '{ruim[:60]}'")

    # Telefones
    for campo, rot in (("celular", "Celular"), ("fone", "Fone")):
        d[campo] = None
        if campos.get(campo):
            try:
                d[campo] = nz.normalize_phone(campos[campo])
            except ValueError:
                r.motivos.append(f"{rot} inválido ignorado: '{campos[campo][:40]}'")

    # CEP
    d["cep"] = None
    if campos.get("cep"):
        try:
            d["cep"] = nz.normalize_cep(campos["cep"])
            if len(d["cep"]) < 8:
                r.motivos.append(f"CEP incompleto ('{campos['cep'][:20]}'): confira o endereço.")
        except ValueError:
            r.motivos.append(f"CEP inválido ignorado: '{campos['cep'][:30]}'")

    # Cidade / Estado ("Olinda - Pernambuco")
    cidade, uf_cidade = campos.get("cidade", ""), None
    m = re.match(r"^(.*\S)\s+-\s+([^-]+)$", cidade)
    if m and uf_de(m.group(2)):
        cidade, uf_cidade = m.group(1).strip(), uf_de(m.group(2))
    uf_col = None
    if campos.get("estado"):
        uf_col = uf_de(campos["estado"])
        if not uf_col:
            r.motivos.append(f"Estado não reconhecido ignorado: '{campos['estado'][:30]}'")
    if uf_col and uf_cidade and uf_col != uf_cidade:
        r.avisos.append(f"Estado da coluna ({uf_col}) difere do indicado na cidade ({uf_cidade}); usado o da coluna.")
    d["estado"] = uf_col or uf_cidade
    d["cidade"] = cidade or None

    # Textos livres (com neutralização de fórmulas)
    for campo in ("nome", "nome_fantasia", "rua", "numero", "complemento", "bairro"):
        v = campos.get(campo) or None
        n = neutralizar(v)
        if n != v:
            r.avisos.append(f"Campo '{campo}' começava com caractere de fórmula; neutralizado.")
        d[campo] = n
    d["cidade"] = neutralizar(d["cidade"])
    d["numero_base"] = nz.numero_base(d["numero"])
    faltam = [rot for campo, rot in (("rua", "rua"), ("cidade", "cidade")) if not d.get(campo)]
    if faltam:  # sem isso o mapa não consegue localizar a empresa
        r.motivos.append("Endereço incompleto para localizar no mapa (falta: " + ", ".join(faltam) + ").")
    return r


def completude(d: dict) -> tuple[int, int]:
    """Quanto mais campos válidos preenchidos, mais completo (desempate: mais texto)."""
    n = sum(1 for k in CAMPOS_INFO if d.get(k) not in (None, "", []))
    return n, sum(len(str(d.get(k) or "")) for k in CAMPOS_INFO)


def chave_sem_cnpj(d: dict) -> str:
    return "|".join(_k(str(d.get(k) or "")) for k in ("nome", "rua", "numero", "cidade"))
