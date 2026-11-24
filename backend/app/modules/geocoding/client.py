"""Cliente do Nominatim (OpenStreetMap), com as proteções que a política de uso e a segurança exigem:
- destino FIXO vindo da configuração; endereços de empresas só vão como parâmetros codificados (nunca formam host/caminho);
- só https (ou localhost se GEOCODER_ALLOW_LOCAL=true, p.ex. um Nominatim próprio); bloqueia localhost, redes privadas,
  link-local e serviços de metadados (169.254.169.254); sem credenciais na URL; SEM seguir redirecionamentos (SSRF);
- no máximo 1 consulta por segundo, timeout curto, resposta limitada em tamanho, User-Agent identificando o sistema."""
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MAX_RESPOSTA = 256 * 1024


class GeocoderConfigInvalida(Exception):
    pass


class GeocoderIndisponivel(Exception):
    """Falha de rede/serviço (timeout, 5xx, 429...). NÃO significa 'endereço não encontrado'."""


def _ip_proibido(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified


def validar_destino(url: str, permitir_local: bool = False) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url)
    if u.username or u.password:
        raise GeocoderConfigInvalida("A URL do geocodificador não pode ter usuário/senha.")
    if u.scheme not in ("https", "http") or not u.hostname:
        raise GeocoderConfigInvalida("A URL do geocodificador deve ser http(s).")
    if u.scheme == "http" and not permitir_local:
        raise GeocoderConfigInvalida("A URL do geocodificador deve usar https.")
    host = u.hostname.lower().rstrip(".")
    if not permitir_local and (host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))):
        raise GeocoderConfigInvalida("O destino do geocodificador aponta para rede interna e foi bloqueado.")
    try:  # IP escrito direto na URL é checado como está, sem depender do DNS
        literal = {str(ipaddress.ip_address(host))}
    except ValueError:
        literal = None
    try:
        ips = literal or {i[4][0] for i in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)}
    except OSError:
        raise GeocoderIndisponivel("Não foi possível resolver o endereço do geocodificador.")
    if not permitir_local and any(_ip_proibido(ip) for ip in ips):
        raise GeocoderConfigInvalida("O destino do geocodificador aponta para rede interna e foi bloqueado.")
    if permitir_local and u.scheme == "http" and not all(ipaddress.ip_address(ip).is_loopback for ip in ips):
        raise GeocoderConfigInvalida("http só é aceito para localhost.")
    return u


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # 30x vira erro: um redirecionamento poderia levar a um destino interno


@dataclass
class Resposta:
    itens: list[dict]


class Nominatim:
    def __init__(self, base_url: str, email: str = "", min_intervalo: float = 1.1, timeout: float = 10.0,
                 permitir_local: bool = False, sleeper=time.sleep, relogio=time.monotonic, abrir=None):
        self.base = base_url.rstrip("/")
        self.email = email if re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email or "") else ""  # nunca envia lixo
        self.min_intervalo = min_intervalo
        self.timeout = timeout
        self.permitir_local = permitir_local
        self._sleep, self._clock = sleeper, relogio
        self._ultimo: float | None = None
        self._abrir = abrir or urllib.request.build_opener(_SemRedirect).open
        self.ua = "SindnorteEmpresas/1.0 (uso interno do Sindnorte" + (f"; contato: {self.email})" if self.email else ")")

    def montar_url(self, consulta: dict[str, str]) -> str:
        params = {"format": "jsonv2", "addressdetails": "1", "limit": "1", "countrycodes": "br", "accept-language": "pt-BR"}
        params.update({k: v for k, v in consulta.items() if v})
        if self.email:
            params["email"] = self.email
        return f"{self.base}/search?" + urllib.parse.urlencode(params)  # tudo codificado: o endereço nunca vira host/caminho

    def buscar(self, consulta: dict[str, str]) -> Resposta:
        url = self.montar_url(consulta)
        validar_destino(url, self.permitir_local)
        if self._ultimo is not None:  # respeita 1 consulta por segundo
            espera = self.min_intervalo - (self._clock() - self._ultimo)
            if espera > 0:
                self._sleep(espera)
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "application/json"})
        self._ultimo = self._clock()
        try:
            with self._abrir(req, timeout=self.timeout) as r:
                corpo = r.read(MAX_RESPOSTA + 1)
        except urllib.error.HTTPError as e:
            raise GeocoderIndisponivel(f"O serviço respondeu com erro {e.code}.")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GeocoderIndisponivel("Não foi possível falar com o serviço de geocodificação.")
        if len(corpo) > MAX_RESPOSTA:
            raise GeocoderIndisponivel("Resposta grande demais do serviço de geocodificação.")
        try:
            dados = json.loads(corpo)
        except ValueError:
            raise GeocoderIndisponivel("Resposta inválida do serviço de geocodificação.")
        if not isinstance(dados, list):
            raise GeocoderIndisponivel("Resposta inesperada do serviço de geocodificação.")
        return Resposta([d for d in dados if isinstance(d, dict)])
