"""Leitura SEGURA de .xlsx e .xls. O arquivo é tratado como entrada não confiável:
- nunca é gravado em disco nem executado; é lido em memória;
- a estrutura do ZIP é validada ANTES de abrir (zip bomb, path traversal, macros, vínculos externos);
- o XML é lido com defusedxml (bloqueia 'billion laughs' / XXE);
- fórmulas não são calculadas (data_only=True: só valores já gravados);
- .xls (formato antigo, binário): lido só com xlrd, que apenas extrai valores (não executa macros nem vínculos)."""
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime

MAX_ENTRADAS_ZIP = 500
MAX_DESCOMPACTADO = 100 * 1024 * 1024
MAX_TAXA_COMPRESSAO = 200
MAX_LINHAS = 20000
MAX_COLUNAS = 40
MAX_CELULA = 5000

COLUNAS = {  # cabeçalho esperado (como o Asaas exporta) -> campo interno
    "Nome": "nome", "Email": "email", "Emails adicionais": "emails_adicionais", "Celular": "celular",
    "Empresa": "nome_fantasia", "CPF ou CNPJ": "cnpj", "Fone": "fone", "Rua": "rua", "Número": "numero",
    "Complemento": "complemento", "Bairro": "bairro", "Cidade": "cidade", "CEP": "cep", "Estado": "estado",
}
ALIASES = {  # nomes antigos continuam aceitos
    "Nome da Empresa": "nome", "Nome Fantasia": "nome_fantasia", "CNPJ": "cnpj",
}
# Qualquer outra coluna (ex.: "Identificador externo", "Valor vencido", "Valor pago", "Valor a vencer") é IGNORADA e listada na prévia.
ASSINATURA_XLSX = b"PK\x03\x04"
ASSINATURA_XLS = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # contêiner OLE2 do .xls


class ArquivoInvalido(Exception):
    """Mensagem segura (pt-BR) que pode ser mostrada ao usuário."""


def _chave(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


_POR_CHAVE = {_chave(k): (k, v) for k, v in {**COLUNAS, **ALIASES}.items()}
_SUSPEITOS = ("vbaproject.bin", "xl/embeddings/", "xl/externallinks/", "xl/activex/", "xl/ctrlprops/")


def validar_zip(data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise ArquivoInvalido("O arquivo não é uma planilha .xlsx ou .xls válida.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except zipfile.BadZipFile:
        raise ArquivoInvalido("O arquivo está corrompido ou não é uma planilha .xlsx válida.")
    if len(infos) > MAX_ENTRADAS_ZIP:
        raise ArquivoInvalido("Estrutura da planilha inesperada (partes demais).")
    total, nomes = 0, set()
    for i in infos:
        n = i.filename.replace("\\", "/")
        partes = n.split("/")
        if n.startswith("/") or ".." in partes or "\x00" in n or re.match(r"^[A-Za-z]:", n):
            raise ArquivoInvalido("A planilha contém caminhos internos inseguros.")
        if i.flag_bits & 0x1:
            raise ArquivoInvalido("Planilhas protegidas por senha não são aceitas.")
        low = n.lower()
        if any(s in low for s in _SUSPEITOS):
            raise ArquivoInvalido("A planilha contém macros, objetos incorporados ou vínculos externos, que não são aceitos.")
        if low.endswith((".xml", ".rels", ".vml")) and i.file_size:
            try:
                with z.open(i) as parte:
                    ini = parte.read(8192).lower()
            except Exception:
                raise ArquivoInvalido("O arquivo está corrompido.")
            if b"<!doctype" in ini or b"<!entity" in ini:  # planilha legítima nunca tem; é a porta da 'bomba de entidades' (XXE)
                raise ArquivoInvalido("A planilha contém declarações XML não permitidas.")
        total += i.file_size
        if total > MAX_DESCOMPACTADO:
            raise ArquivoInvalido("A planilha é grande demais depois de descompactada.")
        if i.compress_size and i.file_size > 1_000_000 and i.file_size / i.compress_size > MAX_TAXA_COMPRESSAO:
            raise ArquivoInvalido("Taxa de compressão suspeita; o arquivo foi recusado.")
        nomes.add(n)
    if "[Content_Types].xml" not in nomes or "xl/workbook.xml" not in nomes:
        raise ArquivoInvalido("O arquivo não tem a estrutura de uma planilha do Excel (.xlsx).")
    try:
        ct = z.read("[Content_Types].xml")[:200000].decode("utf-8", "ignore")
    except Exception:
        raise ArquivoInvalido("O arquivo está corrompido.")
    if "macroEnabled" in ct:
        raise ArquivoInvalido("Planilhas com macros (.xlsm) não são aceitas.")


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):  # CNPJ gravado como número: 1.28844950001E+13 -> 12884495000100 (sem notação científica)
        return str(int(v)) if v.is_integer() and abs(v) < 1e18 else repr(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)[: MAX_CELULA + 1]


class Planilha:
    def __init__(self):
        self.linhas: list[tuple[int, dict[str, str]]] = []
        self.em_branco = 0
        self.cabecalhos_ignorados: list[str] = []
        self.avisos: list[str] = []


def _abrir_xlsx(data: bytes):
    """Devolve (cabeçalho, iterador de linhas, número de abas, função de fechar)."""
    validar_zip(data)
    from openpyxl import load_workbook  # importado aqui: só carrega quando há upload
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ArquivoInvalido("Não foi possível ler a planilha (arquivo corrompido ou formato não suportado).")
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        try:
            cab = next(it)
        except StopIteration:
            raise ArquivoInvalido("A planilha está vazia.")
    except Exception:
        wb.close()
        raise
    return cab, it, len(wb.worksheets), wb.close


def _abrir_xls(data: bytes):
    import xlrd  # importado aqui: só carrega quando há upload
    try:
        wb = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False)
    except xlrd.XLRDError as e:
        if "encrypt" in str(e).lower() or "password" in str(e).lower():
            raise ArquivoInvalido("Planilhas protegidas por senha não são aceitas.")
        raise ArquivoInvalido("Não foi possível ler a planilha .xls (arquivo corrompido ou formato não suportado).")
    except Exception:
        raise ArquivoInvalido("Não foi possível ler a planilha .xls (arquivo corrompido ou formato não suportado).")
    try:
        if wb.nsheets == 0:
            raise ArquivoInvalido("A planilha está vazia.")
        ws = wb.sheet_by_index(0)
        if ws.nrows == 0:
            raise ArquivoInvalido("A planilha está vazia.")
        if ws.ncols > MAX_COLUNAS:
            raise ArquivoInvalido(f"A planilha tem colunas demais (máximo {MAX_COLUNAS}).")
        if ws.nrows > MAX_LINHAS + 1:
            raise ArquivoInvalido(f"A planilha excede o limite de {MAX_LINHAS} linhas.")

        def valor(c):
            if c.ctype == xlrd.XL_CELL_DATE:
                try:
                    return xlrd.xldate_as_datetime(c.value, wb.datemode)
                except Exception:
                    return ""
            if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                return None
            if c.ctype == xlrd.XL_CELL_BOOLEAN:
                return bool(c.value)
            return c.value

        linhas = ([valor(ws.cell(r, c)) for c in range(ws.ncols)] for r in range(ws.nrows))
        cab = next(linhas)
    except ArquivoInvalido:
        wb.release_resources()
        raise
    except Exception:
        wb.release_resources()
        raise ArquivoInvalido("Não foi possível ler a planilha .xls (conteúdo inválido).")
    return cab, linhas, wb.nsheets, wb.release_resources


def ler_planilha(data: bytes) -> Planilha:
    if data.startswith(ASSINATURA_XLSX):
        cab, it, abas, fechar = _abrir_xlsx(data)
    elif data.startswith(ASSINATURA_XLS):
        cab, it, abas, fechar = _abrir_xls(data)
    else:
        raise ArquivoInvalido("O arquivo não é uma planilha .xlsx ou .xls válida.")
    try:
        p = Planilha()
        if abas > 1:
            p.avisos.append(f"A planilha tem {abas} abas; somente a primeira foi lida.")
        if len(cab) > MAX_COLUNAS:
            raise ArquivoInvalido(f"A planilha tem colunas demais (máximo {MAX_COLUNAS}).")
        mapa: dict[int, str] = {}
        for idx, h in enumerate(cab):
            if h is None or str(h).strip() == "":
                continue
            achado = _POR_CHAVE.get(_chave(h))
            if not achado:
                p.cabecalhos_ignorados.append(str(h)[:60])
            elif achado[1] in mapa.values():
                raise ArquivoInvalido(f"Cabeçalho repetido: {achado[0]}.")
            else:
                mapa[idx] = achado[1]
        faltando = [k for k, v in COLUNAS.items() if v not in mapa.values()]
        if faltando:
            raise ArquivoInvalido("Cabeçalhos ausentes na planilha: " + ", ".join(faltando) + ".")
        try:
            for n, row in enumerate(it, start=2):
                if len(p.linhas) + p.em_branco >= MAX_LINHAS:
                    raise ArquivoInvalido(f"A planilha excede o limite de {MAX_LINHAS} linhas.")
                reg = {campo: _texto(row[i]) if i < len(row) else "" for i, campo in mapa.items()}
                if not any(v.strip() for v in reg.values()):
                    p.em_branco += 1
                    continue
                p.linhas.append((n, reg))
        except ArquivoInvalido:
            raise
        except Exception:
            raise ArquivoInvalido("Não foi possível ler as linhas da planilha (conteúdo inválido).")
        if not p.linhas:
            raise ArquivoInvalido("A planilha não tem nenhuma linha de dados.")
        return p
    finally:
        fechar()
