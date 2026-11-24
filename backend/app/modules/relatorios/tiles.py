"""Fundo de ruas para o mapa do PDF: baixa (e guarda em cache) os blocos de mapa do OpenStreetMap e os monta numa imagem.
Segurança/política de uso: destino FIXO da configuração (https, sem localhost/rede interna, sem redirecionamento), x/y/z só números,
tamanho e formato do bloco verificados (PNG 256×256), User-Agent identificando o sistema, cache em disco (≥ 7 dias) e poucos blocos por mapa.
Se não houver internet, devolve None e o PDF usa o mapa esquemático."""
import io
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import get_settings
from app.modules.geocoding.client import GeocoderConfigInvalida, GeocoderIndisponivel, _SemRedirect, validar_destino

TILE = 256
MAX_BYTES = 300 * 1024
Z_MIN, Z_MAX, Z_UNICO = 10, 18, 17
MAX_BLOCOS = 30
FALTANDO_MAX = 0.25  # se mais de 25% dos blocos falharem, não vale a pena: usa o mapa esquemático
MODELO_OK = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?/[^\s{}]*\{z\}[^\s{}]*\{x\}[^\s{}]*\{y\}[^\s{}]*$")


@dataclass
class BaseMapa:
    jpeg: bytes
    w: int
    h: int
    z: int
    left: float  # coordenada (px de mundo, Web Mercator) do canto superior esquerdo da imagem
    top: float

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        px, py = _mundo(lat, lon, self.z)
        return px - self.left, py - self.top

    def metros_por_px(self, lat: float) -> float:
        return 156543.03392 * math.cos(math.radians(lat)) / (2 ** self.z)


def _mundo(lat: float, lon: float, z: int) -> tuple[float, float]:
    lat = max(-85.0511, min(85.0511, lat))
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _ua() -> str:
    e = get_settings().geocoder_email
    return "SindnorteEmpresas/1.0 (uso interno do Sindnorte" + (f"; contato: {e})" if e else ")")


def _url(z: int, x: int, y: int) -> str:
    modelo = get_settings().tiles_url
    if not MODELO_OK.match(modelo):
        raise GeocoderConfigInvalida("TILES_URL inválida: use https://servidor/{z}/{x}/{y}.png")
    return modelo.replace("{z}", str(int(z))).replace("{x}", str(int(x))).replace("{y}", str(int(y)))


def _validar_png(dados: bytes) -> bytes:
    if len(dados) > MAX_BYTES or not dados.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GeocoderIndisponivel("Bloco de mapa inválido.")
    try:
        im = Image.open(io.BytesIO(dados))
        im.verify()
        if im.size != (TILE, TILE):
            raise GeocoderIndisponivel("Bloco de mapa com tamanho inesperado.")
    except GeocoderIndisponivel:
        raise
    except Exception:
        raise GeocoderIndisponivel("Bloco de mapa ilegível.")
    return dados


def baixar_bloco(z: int, x: int, y: int) -> bytes:
    url = _url(z, x, y)
    validar_destino(url)
    req = urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "image/png"})
    try:
        with urllib.request.build_opener(_SemRedirect).open(req, timeout=8) as r:
            return _validar_png(r.read(MAX_BYTES + 1))
    except urllib.error.HTTPError as e:
        raise GeocoderIndisponivel(f"Servidor de mapas respondeu {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GeocoderIndisponivel("Servidor de mapas indisponível.")


def _com_cache(z: int, x: int, y: int, buscador) -> bytes:
    base = Path(get_settings().tiles_dir).resolve()
    arq = (base / str(z) / str(x) / f"{y}.png").resolve()
    if base not in arq.parents:  # z/x/y são inteiros; defesa extra contra caminho fora da pasta
        raise GeocoderIndisponivel("Caminho de cache inválido.")
    ttl = get_settings().tiles_cache_dias * 86400
    try:
        if arq.is_file() and time.time() - arq.stat().st_mtime < ttl:
            return _validar_png(arq.read_bytes())
    except (OSError, GeocoderIndisponivel):
        pass
    dados = buscador(z, x, y)
    try:
        arq.parent.mkdir(parents=True, exist_ok=True)
        tmp = arq.with_suffix(".tmp")
        tmp.write_bytes(dados)
        tmp.replace(arq)
    except OSError:
        pass  # cache é só otimização
    return dados


def escolher_zoom(pontos: list[tuple[float, float]], w: int, h: int) -> int | None:
    if len(pontos) == 1 or max(p[0] for p in pontos) - min(p[0] for p in pontos) < 1e-5 and max(p[1] for p in pontos) - min(p[1] for p in pontos) < 1e-5:
        return Z_UNICO
    for z in range(Z_MAX, Z_MIN - 1, -1):
        xs, ys = zip(*(_mundo(la, lo, z) for la, lo in pontos))
        if max(xs) - min(xs) <= w * 0.82 and max(ys) - min(ys) <= h * 0.82:
            return min(z, Z_UNICO)
    return None  # área grande demais para mapa de ruas


def montar_base(pontos: list[tuple[float, float]], w: int = 1030, h: int = 580, buscador=None, prazo_s: float = 25.0) -> BaseMapa | None:
    """Imagem de ruas centrada nos pontos, ou None (sem pontos, área grande, sem internet, configuração desligada/ inválida)."""
    s = get_settings()
    if not s.pdf_mapa_ruas or not pontos:
        return None
    z = escolher_zoom(pontos, w, h)
    if z is None:
        return None
    xs, ys = zip(*(_mundo(la, lo, z) for la, lo in pontos))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    left, top = cx - w / 2, cy - h / 2
    tx0, tx1 = int(left // TILE), int((left + w - 1) // TILE)
    ty0, ty1 = int(top // TILE), int((top + h - 1) // TILE)
    total = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if total > MAX_BLOCOS:
        return None
    lim = 2 ** z
    buscador = buscador or baixar_bloco
    tela = Image.new("RGB", (w, h), (226, 232, 240))
    faltam, fim = 0, time.monotonic() + prazo_s
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            if not (0 <= ty < lim):
                continue
            try:
                if time.monotonic() > fim:
                    raise GeocoderIndisponivel("Tempo esgotado.")
                dados = _com_cache(z, tx % lim, ty, buscador)
                tela.paste(Image.open(io.BytesIO(dados)).convert("RGB"), (int(tx * TILE - left), int(ty * TILE - top)))
            except (GeocoderIndisponivel, GeocoderConfigInvalida):
                faltam += 1
    if faltam / total > FALTANDO_MAX:
        return None
    buf = io.BytesIO()
    tela.save(buf, "JPEG", quality=82)
    return BaseMapa(buf.getvalue(), w, h, z, left, top)
