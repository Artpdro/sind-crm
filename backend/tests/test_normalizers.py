import pytest

from app.modules.companies.normalizers import (cnpj_dv_valid, normalize_cep, normalize_cnpj, normalize_phone,
                                               numero_base)


def test_cnpj_formatado_vira_14_digitos_texto():
    assert normalize_cnpj("12.345.678/0001-95") == "12345678000195"
    assert isinstance(normalize_cnpj("12.345.678/0001-95"), str)


def test_cnpj_zeros_a_esquerda_preservados():
    assert normalize_cnpj("02.786.558/0021-13") == "02786558002113"  # da planilha real


def test_cnpj_vazio_vira_none():
    assert normalize_cnpj("") is None and normalize_cnpj(None) is None and normalize_cnpj("  ") is None


@pytest.mark.parametrize("bad", ["123", "19882742491", "12345678000190", "11111111111111", "1234567800019500"])
def test_cnpj_invalido_rejeitado(bad):
    with pytest.raises(ValueError):
        normalize_cnpj(bad)


def test_cnpj_notacao_cientifica_nao_e_aceita():
    with pytest.raises(ValueError):
        normalize_cnpj("1.28844950001E+13")


def test_dv():
    assert cnpj_dv_valid("12884495000148")


def test_cep():
    assert normalize_cep("53300-090") == "53300090"
    assert normalize_cep("5365081") == "5365081"  # incompleto: preservado, não inventado
    assert normalize_cep("") is None
    with pytest.raises(ValueError):
        normalize_cep("123456789")


def test_telefone():
    assert normalize_phone("(81) 99558-3225") == "81995583225"
    assert normalize_phone("+55 81 99558-3225") == "81995583225"
    assert normalize_phone("") is None
    with pytest.raises(ValueError):
        normalize_phone("123")


@pytest.mark.parametrize("raw,esperado", [
    ("10", 10), ("10A", 10), ("10-B", 10), ("100/102", 100), ("Loja 01", 1), ("07 A", 7),
    ("S/N", None), ("SN", None), ("s/n", None), ("Fundos", None), ("", None), (None, None),
    ("12345678901234", None),
])
def test_numero_base(raw, esperado):
    assert numero_base(raw) == esperado


def test_regressao_texto_sem_digitos_nao_vira_vazio():
    # Bug: "abc" era tratado como campo vazio e aceito. Deve ser rejeitado; só vazio de verdade vira None.
    for fn in (normalize_cnpj, normalize_cep, normalize_phone):
        with pytest.raises(ValueError):
            fn("abc")
        with pytest.raises(ValueError):
            fn("--/..")
        assert fn("   ") is None and fn("") is None and fn(None) is None
