"""Consulta da SITUAÇÃO CADASTRAL de um CNPJ na base da Receita Federal.

A Receita não oferece API pública gratuita (o site oficial exige captcha). Por isso usamos um serviço público que
espelha a base da Receita (padrão: BrasilAPI). Cuidados de segurança, no mesmo padrão do geocodificador:
- a consulta sai SEMPRE do servidor (o navegador nunca fala com o serviço externo: nada de CNPJ vazando por terceiros);
- destino fixo vindo da configuração, só https, bloqueia rede interna/metadados, SEM seguir redirecionamentos (SSRF);
- o CNPJ só entra na URL depois de validado (14 dígitos + dígito verificador);
- timeout curto, resposta limitada em tamanho, mensagens de erro sem repetir o conteúdo do serviço;
- do que o serviço devolve guardamos/repassamos SOMENTE a situação cadastral (nada de sócios, endereço, telefones...)."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.modules.geocoding.client import MAX_RESPOSTA, GeocoderConfigInvalida, GeocoderIndisponivel, _SemRedirect, validar_destino

# Tabela oficial de códigos da Receita: 01 NULA, 02 ATIVA, 03 SUSPENSA, 04 INAPTA, 08 BAIXADA.
POR_CODIGO = {1: "inativa", 2: "ativa", 3: "inativa", 4: "inapta", 8: "inativa"}
DESCRICAO_POR_CODIGO = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}
POR_DESCRICAO = {"NULA": "inativa", "ATIVA": "ativa", "SUSPENSA": "inativa", "INAPTA": "inapta", "BAIXADA": "inativa"}
SITUACOES = ("ativa", "inativa", "inapta")


class ReceitaIndisponivel(Exception):
    """Falha de rede/serviço, resposta inesperada ou configuração inválida. NÃO significa 'CNPJ inexistente'."""


class CnpjNaoEncontrado(Exception):
    pass


@dataclass(frozen=True)
class Situacao:
    situacao: str   # ativa | inativa | inapta
    descricao: str  # texto da Receita (ATIVA, INAPTA, BAIXADA, SUSPENSA, NULA)


def classificar(dados: object) -> Situacao:
    if not isinstance(dados, dict):
        raise ReceitaIndisponivel("Resposta inesperada do serviço da Receita.")
    desc = str(dados.get("descricao_situacao_cadastral") or "").strip().upper()[:40]
    cod = dados.get("situacao_cadastral")
    por_cod = POR_CODIGO.get(cod) if isinstance(cod, int) and not isinstance(cod, bool) else None
    por_desc = POR_DESCRICAO.get(desc)
    if por_cod and por_desc and por_cod != por_desc:
        raise ReceitaIndisponivel("Resposta inconsistente do serviço da Receita.")
    situacao = por_cod or por_desc
    if not situacao:
        raise ReceitaIndisponivel("Resposta inesperada do serviço da Receita.")
    if not desc:
        desc = DESCRICAO_POR_CODIGO[cod]
    return Situacao(situacao, desc)


def consultar(cnpj14: str, url_modelo: str, timeout: float = 8.0, abrir=None) -> Situacao:
    if len(cnpj14) != 14 or not cnpj14.isdigit():  # defesa extra: o CNPJ já vem validado, mas aqui ele vira parte da URL
        raise ValueError("CNPJ inválido.")
    if "{cnpj}" not in url_modelo:
        raise ReceitaIndisponivel("Serviço da Receita mal configurado.")
    url = url_modelo.replace("{cnpj}", cnpj14)
    try:
        validar_destino(url)
    except (GeocoderConfigInvalida, GeocoderIndisponivel):
        raise ReceitaIndisponivel("Serviço da Receita indisponível ou mal configurado.")
    req = urllib.request.Request(url, headers={"User-Agent": "SindnorteEmpresas/1.0 (uso interno)", "Accept": "application/json"})
    opener = abrir or urllib.request.build_opener(_SemRedirect).open
    try:
        with opener(req, timeout=timeout) as r:
            corpo = r.read(MAX_RESPOSTA + 1)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise CnpjNaoEncontrado()
        raise ReceitaIndisponivel(f"O serviço da Receita respondeu com erro {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ReceitaIndisponivel("Não foi possível falar com o serviço da Receita.")
    if len(corpo) > MAX_RESPOSTA:
        raise ReceitaIndisponivel("Resposta grande demais do serviço da Receita.")
    try:
        dados = json.loads(corpo)
    except ValueError:
        raise ReceitaIndisponivel("Resposta inválida do serviço da Receita.")
    return classificar(dados)
