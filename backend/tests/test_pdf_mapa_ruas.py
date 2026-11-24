"""Mapa de ruas no PDF: blocos do OpenStreetMap (aqui, falsos), cache, projeção, segurança do download e fallback."""
import io

import pytest
from PIL import Image, ImageDraw

from app.config import get_settings
from app.modules.geocoding.client import GeocoderConfigInvalida, GeocoderIndisponivel
from app.modules.relatorios import tiles
from tests.test_relatorios import cli, dados, texto  # noqa: F401


def png(cor=(200, 220, 200), tam=256) -> bytes:
    im = Image.new("RGB", (tam, tam), cor)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, tam - 1, tam - 1], outline=(120, 120, 120))
    d.line([0, 128, tam, 128], fill=(255, 255, 255), width=6)  # "rua"
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


class Fake:
    def __init__(self, falha=lambda z, x, y: False):
        self.chamadas, self.falha = [], falha

    def __call__(self, z, x, y):
        self.chamadas.append((z, x, y))
        if self.falha(z, x, y):
            raise GeocoderIndisponivel("fora do ar")
        return png()


@pytest.fixture
def ligado():
    get_settings().pdf_mapa_ruas = True
    yield
    get_settings().pdf_mapa_ruas = False


PTS = [(-7.9400, -34.8700), (-7.9420, -34.8725)]


def test_monta_imagem_centrada_e_projeta_pontos_dentro(ligado):
    f = Fake()
    b = tiles.montar_base(PTS, buscador=f)
    assert b and b.w == 1030 and b.h == 580 and 10 <= b.z <= 17
    assert Image.open(io.BytesIO(b.jpeg)).size == (1030, 580)
    for la, lo in PTS:
        x, y = b.xy(la, lo)
        assert 0 < x < b.w and 0 < y < b.h
    assert len(f.chamadas) <= tiles.MAX_BLOCOS


def test_zoom_maior_para_area_menor_e_um_ponto_usa_zoom_fixo(ligado):
    pequeno = tiles.escolher_zoom([(-7.9400, -34.8700), (-7.9402, -34.8702)], 1030, 580)
    grande = tiles.escolher_zoom([(-7.90, -34.90), (-8.10, -34.80)], 1030, 580)
    assert pequeno > grande
    assert tiles.escolher_zoom([(-7.94, -34.87)], 1030, 580) == tiles.Z_UNICO
    assert tiles.escolher_zoom([(-7.94, -34.87), (-7.94, -34.87)], 1030, 580) == tiles.Z_UNICO


def test_area_enorme_nao_tenta_mapa_de_ruas(ligado):
    f = Fake()
    assert tiles.montar_base([(-8.0, -35.0), (-23.5, -46.6)], buscador=f) is None
    assert f.chamadas == []


def test_cache_em_disco_evita_baixar_de_novo(ligado):
    f1, f2 = Fake(), Fake()
    assert tiles.montar_base(PTS, buscador=f1)
    assert f1.chamadas
    assert tiles.montar_base(PTS, buscador=f2)
    assert f2.chamadas == []


def test_falha_total_ou_acima_de_25_por_cento_cai_no_esquematico(ligado):
    assert tiles.montar_base(PTS, buscador=Fake(lambda *a: True)) is None
    n = {"i": 0}

    def metade(z, x, y):
        n["i"] += 1
        return n["i"] % 2 == 0
    assert tiles.montar_base(PTS, buscador=Fake(metade)) is None


def test_poucas_falhas_ainda_gera_o_mapa(ligado):
    n = {"i": 0}

    def uma(z, x, y):
        n["i"] += 1
        return n["i"] == 1
    assert tiles.montar_base([(-7.94, -34.87)], buscador=Fake(uma)) is not None


def test_desligado_ou_sem_pontos(ligado):
    assert tiles.montar_base([], buscador=Fake()) is None
    get_settings().pdf_mapa_ruas = False
    assert tiles.montar_base(PTS, buscador=Fake()) is None


# ───────── segurança do download ─────────
@pytest.mark.parametrize("url", ["http://tile.openstreetmap.org/{z}/{x}/{y}.png", "https://localhost/{z}/{x}/{y}.png",
                                 "https://127.0.0.1/{z}/{x}/{y}.png", "https://169.254.169.254/{z}/{x}/{y}.png",
                                 "https://10.0.0.5/{z}/{x}/{y}.png", "https://user:pw@tile.osm.org/{z}/{x}/{y}.png", "file:///etc/passwd", "https://a.org/sem-modelo.png"])
def test_url_de_blocos_insegura_e_recusada(url):
    antigo = get_settings().tiles_url
    get_settings().tiles_url = url
    try:
        with pytest.raises((GeocoderConfigInvalida, GeocoderIndisponivel)):
            tiles.baixar_bloco(10, 1, 1)
    finally:
        get_settings().tiles_url = antigo


def test_bloco_que_nao_e_png_ou_tem_tamanho_errado_ou_e_enorme_e_recusado():
    for lixo in (b"<html>oi</html>", b"\x89PNG\r\n\x1a\n" + b"x" * 50, png(tam=64), b"\x89PNG\r\n\x1a\n" + b"0" * (tiles.MAX_BYTES + 5)):
        with pytest.raises(GeocoderIndisponivel):
            tiles._validar_png(lixo)
    assert tiles._validar_png(png())


def test_cache_nao_escreve_fora_da_pasta(ligado, tmp_path):
    with pytest.raises(Exception):
        tiles._com_cache(10, "../../x", 1, Fake())  # type: ignore[arg-type]
    assert not list(tmp_path.glob("**/x*"))


# ───────── PDF ─────────
def test_pdf_com_mapa_de_ruas(cli, dados, ligado, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tiles, "baixar_bloco", Fake())
    r = cli.get("/api/relatorios/empresas.pdf", params={"rua": "Marcondes"})
    t = texto(r)
    assert "colaboradores do OpenStreetMap" in t and "Mapa esquemático" not in t and "exibindo o mapa esquemático" not in t
    assert len(r.content) < 3_000_000


def test_pdf_sem_internet_cai_no_esquematico_com_aviso(cli, dados, ligado, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tiles, "baixar_bloco", Fake(lambda *a: True))
    t = texto(cli.get("/api/relatorios/empresas.pdf", params={"rua": "Marcondes"}))
    assert "Mapa esquemático" in t and "exibindo o mapa esquemático" in t


def test_pdf_so_com_empresas_sem_localizacao_nao_tenta_baixar(cli, owner_engine, ligado, monkeypatch):  # noqa: F811
    from tests.test_mapa import E
    with owner_engine.begin() as db:
        E(db, "Sem loc", "1", lat=None, lon=None)
    f = Fake()
    monkeypatch.setattr(tiles, "baixar_bloco", f)
    t = texto(cli.get("/api/relatorios/empresas.pdf"))
    assert f.chamadas == [] and "Nenhuma empresa desta busca tem localização" in t
