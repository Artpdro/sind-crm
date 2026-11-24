"""CNEFE (IBGE): importação segura + localização exata por número / interpolada / pela rua, antes do OpenStreetMap."""
import io
import zipfile

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.modules.geocoding import cnefe, service as svc

CAB = "COD_UNICO_ENDERECO;COD_UF;COD_MUNICIPIO;CEP;NOM_TIPO_SEGLOGR;NOM_TITULO_SEGLOGR;NOM_SEGLOGR;NUM_ENDERECO;LATITUDE;LONGITUDE"
RUA = ("RUA", "DOUTOR", "LUIS IGNACIO DE ANDRADE LIMA")


def linha(i, num, lat, lon, rua=RUA, cep="53407000", mun=2610707):
    return f"{i};26;{mun};{cep};{rua[0]};{rua[1]};{rua[2]};{num};{lat};{lon}"


def csv_basico():
    # lado par e ímpar, 1 número por ~11 m (0.0001° de latitude)
    ls = [linha(n, n, -7.9 - n * 0.00001, -34.85 - n * 0.00001) for n in (1195, 1197, 1199, 1201, 1203, 1207, 1209, 1211)]
    ls += [linha(100, 1205, -7.912000, -34.862000), linha(101, 0, -7.9, -34.85), linha(102, "S/N", -7.9, -34.85)]
    ls += [linha(200 + n, n, -7.95 - n * 0.00002, -34.9, rua=("AVENIDA", "", "BEIRA MAR")) for n in (10, 20, 30)]
    return "\n".join([CAB] + ls) + "\n"


def zip_de(conteudo, tmp_path, nome="2610707_PAULISTA.zip", interno="a.csv"):
    p = tmp_path / nome
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(interno, conteudo)
    return p


@pytest.fixture
def base(tmp_path):
    cnefe.importar(zip_de(csv_basico(), tmp_path), saida=lambda *a: None)
    return tmp_path


def emp(**kw):
    e = {"rua": "Rua Doutor Luís Ignácio de Andrade Lima", "numero": "1205", "numero_base": 1205, "bairro": "Janga", "cidade": "Paulista",
         "estado": "PE", "cep": "53407000"}
    e.update(kw)
    return e


def test_importacao_conta_e_descarta_linhas_sem_numero(tmp_path):
    r = cnefe.importar(zip_de(csv_basico(), tmp_path), saida=lambda *a: None)
    assert r["nome"] == "Paulista" and r["pontos"] == 12 and r["descartadas"] == 2
    assert cnefe.estado()["pontos"] == 12


def test_reimportar_substitui_em_vez_de_duplicar(base):
    cnefe.importar(zip_de(csv_basico(), base, nome="2610707_PAULISTA2.zip"), saida=lambda *a: None)
    assert cnefe.estado()["pontos"] == 12


def test_numero_exato_do_exemplo_do_usuario(base):
    r = cnefe.localizar(emp())
    assert r["precisao"] == "endereco" and r["status"] == "localizado" and r["fonte"] == "cnefe"
    assert (r["lat"], r["lon"]) == (-7.912, -34.862)


def test_nome_da_rua_com_abreviacao_sem_titulo_e_sem_acento_acha_a_mesma_rua(base):
    for rua in ("R. Dr. Luis Ignacio de Andrade Lima", "Rua Luís Ignácio de Andrade Lima", "RUA DOUTOR LUIS IGNACIO DE ANDRADE LIMA, 1205 - Janga"):
        assert cnefe.localizar(emp(rua=rua))["precisao"] == "endereco", rua


def test_numero_complemento_usa_numero_base(base):
    assert cnefe.localizar(emp(numero="1205 A", numero_base=1205))["precisao"] == "endereco"


def test_numero_ausente_e_interpolado_entre_vizinhos_do_mesmo_lado(base):
    r = cnefe.localizar(emp(numero="1205", numero_base=1205))  # existe; teste com 1205 removido abaixo
    r = cnefe.localizar(emp(numero="1213", numero_base=1213))
    assert r["precisao"] == "interpolado" and "1211" in r["obs"]  # só um lado: usa o vizinho próximo
    r = cnefe.localizar(emp(numero="1202", numero_base=1202))  # par: vizinhos pares? só 1201/1203 (ímpares) -> qualquer lado
    assert r["precisao"] == "interpolado"


def test_interpolacao_fica_entre_os_dois_vizinhos(base):
    # 1205 fica exato; para testar interpolação real apaga o exato
    c = cnefe._abrir(somente_leitura=False)
    c.execute("DELETE FROM pontos WHERE num = 1205")
    c.commit()
    c.close()
    cnefe.limpar_cache()
    r = cnefe.localizar(emp())
    la1, la2 = -7.9 - 1203 * 0.00001, -7.9 - 1207 * 0.00001
    assert r["precisao"] == "interpolado" and min(la1, la2) <= r["lat"] <= max(la1, la2)
    assert "1203" in r["obs"] and "1207" in r["obs"]


def test_numero_longe_da_base_cai_para_a_rua(base):
    r = cnefe.localizar(emp(numero="5000", numero_base=5000))
    assert r["precisao"] == "rua" and r["status"] == "localizado" and "5000" in r["obs"]


def test_sem_numero_s_n_usa_a_rua(base):
    r = cnefe.localizar(emp(numero="S/N", numero_base=None))
    assert r["precisao"] == "rua"


def test_rua_inexistente_ou_cidade_nao_importada_devolve_none_para_cair_no_osm(base):
    assert cnefe.localizar(emp(rua="Rua Que Nao Existe Mesmo")) is None
    assert cnefe.localizar(emp(cidade="Olinda")) is None
    assert cnefe.localizar(emp(rua="Paulista")) is None  # 'rua' que é o nome do município
    assert cnefe.localizar(emp(rua=None)) is None


def test_sem_base_importada_devolve_none(tmp_path):
    assert cnefe.localizar(emp()) is None and cnefe.estado()["ativo"] is False


def test_desligado_por_configuracao(base):
    get_settings().cnefe_ativo = False
    try:
        assert cnefe.localizar(emp()) is None
    finally:
        get_settings().cnefe_ativo = True


def test_rodovia_so_pela_rua_vai_para_revisao(tmp_path):
    ls = [linha(i, n, -7.9, -34.85 - n * 0.0001, rua=("RODOVIA", "", "BR 101")) for i, n in enumerate((10, 20, 30))]
    cnefe.importar(zip_de(CAB + "\n" + "\n".join(ls) + "\n", tmp_path), saida=lambda *a: None)
    r = cnefe.localizar(emp(rua="Rodovia BR-101", numero="S/N", numero_base=None))
    assert r["status"] == "revisao"


def test_mesmo_numero_em_pontos_distantes_e_marcado_para_revisao(tmp_path):
    ls = [linha(1, 50, -7.9, -34.85, cep="11111111"), linha(2, 50, -7.99, -34.95, cep="22222222"), linha(3, 52, -7.9, -34.8501, cep="11111111")]
    cnefe.importar(zip_de(CAB + "\n" + "\n".join(ls) + "\n", tmp_path), saida=lambda *a: None)
    r = cnefe.localizar(emp(numero="50", numero_base=50, cep="11111111"))  # o CEP desempata
    assert r["status"] == "localizado" and abs(r["lat"] + 7.9) < 1e-6
    r = cnefe.localizar(emp(numero="50", numero_base=50, cep="99999999"))
    assert r["status"] == "revisao"


def test_arquivo_que_nao_e_cnefe_e_rejeitado_sem_gravar(tmp_path):
    with pytest.raises(cnefe.CnefeInvalido, match="faltam as colunas"):
        cnefe.importar(zip_de("a;b;c\n1;2;3\n", tmp_path), saida=lambda *a: None)
    assert cnefe.estado()["pontos"] == 0


def test_zip_sem_csv_zip_bomb_e_municipio_misturado(tmp_path):
    with pytest.raises(cnefe.CnefeInvalido):
        cnefe.importar(zip_de("x", tmp_path, interno="a.txt"), saida=lambda *a: None)
    with pytest.raises(cnefe.CnefeInvalido, match="suspeitos"):
        cnefe.importar(zip_de(CAB + "\n" + ("0" * 5_000_000), tmp_path, nome="b.zip"), saida=lambda *a: None)
    mist = CAB + "\n" + linha(1, 10, -7.9, -34.85) + "\n" + linha(2, 10, -8.0, -34.9, mun=2609600) + "\n"
    with pytest.raises(cnefe.CnefeInvalido, match="mais de um município"):
        cnefe.importar(zip_de(mist, tmp_path, nome="c.zip"), saida=lambda *a: None)
    assert cnefe.estado()["pontos"] == 0  # rollback: nada ficou pela metade


def test_municipio_desconhecido_exige_nome(tmp_path):
    csv = CAB + "\n" + linha(1, 10, -7.9, -34.85, mun=2699999) + "\n"
    with pytest.raises(cnefe.CnefeInvalido, match="--nome"):
        cnefe.importar(zip_de(csv, tmp_path, nome="x.zip"), saida=lambda *a: None)
    assert cnefe.importar(zip_de(csv, tmp_path, nome="x2.zip"), "Cidade Nova", saida=lambda *a: None)["nome"] == "Cidade Nova"


def test_virgula_decimal_e_latin1(tmp_path):
    csv = (CAB + "\n" + linha(1, 10, "-7,9", "-34,85", rua=("RUA", "", "SÃO JOÃO"))).encode("latin-1")
    p = tmp_path / "2610707_P.csv"
    p.write_bytes(csv)
    assert cnefe.importar(p, saida=lambda *a: None)["pontos"] == 1
    assert cnefe.localizar(emp(rua="Rua São João", numero="10", numero_base=10))["lat"] == -7.9


def test_processar_empresa_usa_cnefe_antes_do_osm_e_grava_fonte(base, owner_engine):
    class NaoDeveChamar:
        def buscar(self, *a, **k):
            raise AssertionError("não deveria consultar o OpenStreetMap")
    with owner_engine.connect() as db:
        i = db.execute(text("INSERT INTO empresas (nome, rua, numero, numero_base, bairro, cidade, estado, cep) "
                            "VALUES ('A', 'Rua Doutor Luís Ignácio de Andrade Lima', '1205', 1205, 'Janga', 'Paulista', 'PE', '53407000') RETURNING id")).scalar_one()
        db.commit()
        assert svc.processar_empresa(db, NaoDeveChamar(), i) == "localizado"
        r = db.execute(text("SELECT latitude, geocode_precisao, geocode_fonte, geocode_erro FROM empresas WHERE id=:i"), {"i": i}).one()
    assert tuple(r) == (-7.912, "endereco", "cnefe", None)


def test_rua_fora_do_cnefe_cai_no_osm(base, owner_engine):
    class Geo:
        def __init__(self):
            self.n = 0

        def buscar(self, campos):
            from app.modules.geocoding.client import Resposta
            self.n += 1
            return Resposta([{"lat": "-7.94", "lon": "-34.87", "place_rank": 30, "address": {"house_number": "3", "city": "Paulista", "ISO3166-2-lvl4": "BR-PE"}}])
    geo = Geo()
    with owner_engine.connect() as db:
        i = db.execute(text("INSERT INTO empresas (nome, rua, numero, numero_base, cidade, estado) VALUES ('B', 'Rua Inventada', '3', 3, 'Paulista', 'PE') RETURNING id")).scalar_one()
        db.commit()
        assert svc.processar_empresa(db, geo, i) == "localizado"
        assert db.execute(text("SELECT geocode_fonte FROM empresas WHERE id=:i"), {"i": i}).scalar_one() == "osm"
    assert geo.n >= 1


def test_script_importar_status_aplicar(tmp_path):
    from scripts import cnefe as cli
    saida = []
    p = zip_de(csv_basico(), tmp_path)
    assert cli.main(["importar", str(p)], saida.append) == 0
    assert cli.main(["status"], saida.append) == 0 and any("Paulista" in s and "12" in s for s in saida)
    assert cli.main(["importar", str(tmp_path / "nao_existe.zip")], saida.append) == 1
    assert cli.main(["aplicar"], saida.append) == 0
