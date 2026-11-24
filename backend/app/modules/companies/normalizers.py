"""Normalizadores de dados de empresas. CNPJ, CEP e telefones são SEMPRE texto."""
import re

_NON_DIGITS = re.compile(r"\D")


def digits_only(value: object) -> str:
    return _NON_DIGITS.sub("", "" if value is None else str(value))


def _vazio(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def cnpj_dv_valid(cnpj14: str) -> bool:
    if len(cnpj14) != 14 or not cnpj14.isdigit() or len(set(cnpj14)) == 1:
        return False

    def dv(base: str, weights: list[int]) -> int:
        r = sum(int(d) * w for d, w in zip(base, weights)) % 11
        return 0 if r < 2 else 11 - r

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(cnpj14[:12], w1)
    d2 = dv(cnpj14[:12] + str(d1), [6] + w1)
    return cnpj14[12:] == f"{d1}{d2}"


def normalize_cnpj(value: object) -> str | None:
    """'12.345.678/0001-90' -> '12345678000190'. Vazio -> None.
    Levanta ValueError se não tiver 14 dígitos ou o dígito verificador for inválido."""
    if _vazio(value):
        return None
    d = digits_only(value)
    if not d:  # tem texto, mas nenhum dígito (ex.: "abc"): inválido, não "vazio"
        raise ValueError("CNPJ deve conter números.")
    if len(d) != 14:
        raise ValueError("CNPJ deve ter 14 dígitos.")
    if not cnpj_dv_valid(d):
        raise ValueError("CNPJ inválido (dígito verificador).")
    return d


def normalize_cep(value: object) -> str | None:
    """Mantém apenas dígitos (texto). CEP incompleto é preservado (marcado para revisão), não inventado."""
    if _vazio(value):
        return None
    d = digits_only(value)
    if not d:
        raise ValueError("CEP deve conter números.")
    if len(d) > 8:
        raise ValueError("CEP deve ter no máximo 8 dígitos.")
    return d


def cep_is_complete(cep: str | None) -> bool:
    return bool(cep) and len(cep) == 8


def normalize_phone(value: object) -> str | None:
    """Somente dígitos, como texto. Remove o prefixo 55 se o número ficar com 12-13 dígitos."""
    if _vazio(value):
        return None
    d = digits_only(value)
    if not d:
        raise ValueError("Telefone deve conter números.")
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if not 8 <= len(d) <= 11:
        raise ValueError("Telefone inválido.")
    return d


def numero_base(numero: str | None) -> int | None:
    """Componente numérico do número do endereço (mesma regra da coluna gerada no banco).
    '10A'->10, '100/102'->100, 'S/N'->None, 'Loja 01'->1."""
    if not numero:
        return None
    m = re.search(r"[0-9]+", numero)
    if not m or len(m.group()) > 9:
        return None
    return int(m.group())


def normalize_estado(value: object) -> str | None:
    v = ("" if value is None else str(value)).strip().upper()
    return v or None
