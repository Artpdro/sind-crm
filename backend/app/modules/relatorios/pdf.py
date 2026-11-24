"""Geração do PDF (reportlab, 100% local): critérios, resumo, mapa esquemático desenhado direto no arquivo, e a lista de empresas.
Todo texto vindo do banco passa por `esc()`: o Paragraph do reportlab interpreta marcação tipo XML e não pode receber texto cru."""
import io
import math
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

from reportlab.graphics.shapes import Circle, Drawing, Image, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from PIL import Image as PILImage
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AZUL, DOURADO = colors.HexColor("#215d98"), colors.HexColor("#c29549")  # azul e dourado da logomarca Sindnorte
LOGO = Path(__file__).with_name("logo.png")
APROX = ("interpolado", "rua", "cep", "bairro")
TIPOS = {"ligacao": "Ligação", "whatsapp": "WhatsApp", "email": "E-mail", "visita": "Visita", "reuniao": "Reunião", "atendimento": "Atendimento", "outro": "Outro"}
GEO = {"localizado": "Localizado", "pendente": "Localização pendente", "nao_encontrado": "Endereço não encontrado", "revisao": "Localização precisa de revisão"}
PRECISAO = {"endereco": "endereço exato", "interpolado": "estimada entre vizinhos", "rua": "aproximada pela rua", "cep": "aproximada pelo CEP",
            "bairro": "aproximada pelo bairro", "manual": "corrigida manualmente"}
LARGURA = A4[0] - 30 * mm


def limpar(v) -> str:
    """Texto seguro para as fontes padrão do PDF (Latin-1/cp1252: cobre o português); o que não couber vira '?'. Remove controles."""
    t = "" if v is None else str(v)
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", t)
    return t.encode("cp1252", errors="replace").decode("cp1252")


def esc(v) -> str:
    """Escapa &, < e > (o Paragraph do reportlab interpreta marcação)."""
    return limpar(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cnpj_fmt(c: str | None) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}" if c and len(c) == 14 else (c or "")


def fone_fmt(f: str | None) -> str:
    if not f:
        return ""
    if len(f) in (10, 11):
        return f"({f[:2]}) {f[2:-4]}-{f[-4:]}"
    return f


def cep_fmt(c: str | None) -> str:
    return f"{c[:5]}-{c[5:]}" if c and len(c) == 8 else (c or "")


def data_local(s: str | None) -> str:
    """'2026-10-14 13:05:00.000' (UTC) -> '14/10/2026 10:05' (horário de Recife, UTC-3, sem horário de verão)."""
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc) - timedelta(hours=3)
    except ValueError:
        return limpar(s)[:16]
    return dt.strftime("%d/%m/%Y %H:%M")


def _cor(hex_: str | None) -> colors.Color:
    return colors.HexColor(hex_) if hex_ and re.fullmatch(r"#[0-9a-fA-F]{6}", hex_) else colors.HexColor("#6b7280")


def _estilos():
    b = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=b["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=AZUL, alignment=TA_LEFT, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=b["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=AZUL, spaceBefore=8, spaceAfter=3),
        "n": ParagraphStyle("n", parent=b["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11),
        "s": ParagraphStyle("s", parent=b["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.5),
        "cab": ParagraphStyle("cab", parent=b["BodyText"], fontName="Helvetica-Bold", fontSize=8, textColor=colors.white, leading=10),
        "muted": ParagraphStyle("muted", parent=b["BodyText"], fontName="Helvetica-Oblique", fontSize=7.5, textColor=colors.HexColor("#555555"), leading=9.5),
    }


def _cabecalho(titulo: str, agora: str, usuario: str, st) -> list:
    """Logomarca à esquerda e título à direita (se o arquivo da logo faltar, só o título)."""
    textos = [Paragraph(esc(titulo), st["h1"]), Paragraph(esc(f"Gerado em {agora} por {usuario}"), st["muted"])]
    if not LOGO.is_file():
        return textos + [Spacer(1, 4)]
    larg = 42 * mm
    logo = RLImage(str(LOGO), width=larg, height=larg * 1016 / 2620)
    t = Table([[logo, textos]], colWidths=[larg + 6 * mm, LARGURA - larg - 6 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [t, Spacer(1, 6)]


def _bom_numero(m: float) -> int:
    for n in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000):
        if n >= m:
            return n
    return 100000


def mapa(empresas: list[dict], largura: float = LARGURA, altura: float = 290, base=None) -> Drawing:
    """Mapa esquemático: posição relativa das empresas (projeção equiretangular corrigida pela latitude), com grade, escala e legenda."""
    if base is not None:
        altura = largura * base.h / base.w
    d = Drawing(largura, altura)
    d.add(Rect(0, 0, largura, altura, fillColor=colors.HexColor("#eef3f8"), strokeColor=colors.HexColor("#9fb3c8"), strokeWidth=0.8))
    pts = [e for e in empresas if e.get("latitude") is not None]
    if base is not None and pts:
        return _mapa_ruas(d, empresas, pts, largura, altura, base)
    if not pts:
        d.add(String(largura / 2, altura / 2, "Nenhuma empresa desta busca tem localização no mapa.", textAnchor="middle", fontName="Helvetica", fontSize=9))
        return d
    la = [e["latitude"] for e in pts]
    lo = [e["longitude"] for e in pts]
    lat0, lat1, lon0, lon1 = min(la), max(la), min(lo), max(lo)
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    spanx, spany = max((lon1 - lon0) * kx, 0.004), max(lat1 - lat0, 0.004)  # mínimo ~450 m: um ponto só não vira um mapa "estourado"
    mx, my = 26, 22
    area_w, area_h = largura - 2 * mx, altura - 2 * my - 14
    esc_ = min(area_w / (spanx * 1.16), area_h / (spany * 1.16))  # pt por grau (lat); 16% de folga nas bordas
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    ox, oy = largura / 2, my + 14 + area_h / 2

    def xy(lat, lon):
        return ox + (lon - cx) * kx * esc_, oy + (lat - cy) * esc_

    # grade com rótulos de coordenada (4 linhas em cada direção)
    for i in range(5):
        gy = my + 14 + area_h * i / 4
        gx = mx + area_w * i / 4
        d.add(Line(mx, gy, largura - mx, gy, strokeColor=colors.HexColor("#d3dde8"), strokeWidth=0.4))
        d.add(Line(gx, my + 14, gx, my + 14 + area_h, strokeColor=colors.HexColor("#d3dde8"), strokeWidth=0.4))
        lat_i = cy + (gy - oy) / esc_
        lon_i = cx + (gx - ox) / (kx * esc_)
        d.add(String(mx - 2, gy - 2, f"{lat_i:.4f}", textAnchor="end", fontName="Helvetica", fontSize=5, fillColor=colors.HexColor("#5b6b7c")))
        d.add(String(gx, my + 14 - 8, f"{lon_i:.4f}", textAnchor="middle", fontName="Helvetica", fontSize=5, fillColor=colors.HexColor("#5b6b7c")))
    rotular = len(pts) <= 60
    for n, e in enumerate(empresas, 1):
        if e.get("latitude") is None:
            continue
        x, y = xy(e["latitude"], e["longitude"])
        aprox = e.get("geocode_precisao") in APROX
        d.add(Circle(x, y, 2.6 if aprox else 3.8, fillColor=_cor(e.get("situacao_cor")), strokeColor=colors.HexColor("#e07a00") if e.get("geocode_status") == "revisao" else colors.white, strokeWidth=0.9))
        if rotular:
            d.add(String(x + 4.5, y + 2.5, str(n), fontName="Helvetica-Bold", fontSize=6, fillColor=AZUL))
    # escala (metros por ponto): 1° de latitude ≈ 111 320 m
    m_por_pt = 111320 / esc_
    barra_m = _bom_numero(m_por_pt * 70)
    barra = barra_m / m_por_pt
    ex, ey = largura - mx - barra, 8
    d.add(Line(ex, ey + 3, ex + barra, ey + 3, strokeColor=AZUL, strokeWidth=1.2))
    d.add(Line(ex, ey, ex, ey + 6, strokeColor=AZUL, strokeWidth=1.2))
    d.add(Line(ex + barra, ey, ex + barra, ey + 6, strokeColor=AZUL, strokeWidth=1.2))
    d.add(String(ex + barra / 2, ey + 8, f"{barra_m} m" if barra_m < 1000 else f"{barra_m / 1000:g} km", textAnchor="middle", fontName="Helvetica", fontSize=6.5, fillColor=AZUL))
    d.add(String(mx, altura - 12, "N ↑".replace("↑", "^"), fontName="Helvetica-Bold", fontSize=8, fillColor=AZUL))
    d.add(String(largura - mx, altura - 12, "Mapa esquemático (posição relativa; sem imagem de ruas)", textAnchor="end", fontName="Helvetica-Oblique", fontSize=6.5, fillColor=colors.HexColor("#5b6b7c")))
    return d


def _mapa_ruas(d: Drawing, empresas: list[dict], pts: list[dict], largura: float, altura: float, base) -> Drawing:
    """Mapa com as ruas (imagem do OpenStreetMap) e os pontos das empresas por cima."""
    f = largura / base.w  # pt por pixel da imagem
    d.add(Image(0, 0, largura, altura, PILImage.open(io.BytesIO(base.jpeg)).convert("RGB")))
    d.add(Rect(0, 0, largura, altura, fillColor=None, strokeColor=colors.HexColor("#9fb3c8"), strokeWidth=0.8))
    rotular = len(pts) <= 60
    for n, e in enumerate(empresas, 1):
        if e.get("latitude") is None:
            continue
        px, py = base.xy(e["latitude"], e["longitude"])
        x, y = px * f, altura - py * f
        aprox = e.get("geocode_precisao") in APROX
        d.add(Circle(x, y, 3.2 if aprox else 4.6, fillColor=_cor(e.get("situacao_cor")),
                     strokeColor=colors.HexColor("#e07a00") if e.get("geocode_status") == "revisao" else colors.white, strokeWidth=1.2))
        if rotular:
            for dx, dy in ((-0.6, 0), (0.6, 0), (0, -0.6), (0, 0.6)):  # contorno branco para ler sobre as ruas
                d.add(String(x + 5.5 + dx, y + 3 + dy, str(n), fontName="Helvetica-Bold", fontSize=6.5, fillColor=colors.white))
            d.add(String(x + 5.5, y + 3, str(n), fontName="Helvetica-Bold", fontSize=6.5, fillColor=AZUL))
    lat_c = sum(e["latitude"] for e in pts) / len(pts)
    m_por_pt = base.metros_por_px(lat_c) / f
    barra_m = _bom_numero(m_por_pt * 70)
    barra = barra_m / m_por_pt
    ex, ey = largura - 12 - barra, 12
    d.add(Rect(ex - 6, ey - 8, barra + 12, 26, fillColor=colors.Color(1, 1, 1, 0.85), strokeColor=None))
    d.add(Line(ex, ey + 3, ex + barra, ey + 3, strokeColor=AZUL, strokeWidth=1.2))
    d.add(Line(ex, ey, ex, ey + 6, strokeColor=AZUL, strokeWidth=1.2))
    d.add(Line(ex + barra, ey, ex + barra, ey + 6, strokeColor=AZUL, strokeWidth=1.2))
    d.add(String(ex + barra / 2, ey + 9, f"{barra_m} m" if barra_m < 1000 else f"{barra_m / 1000:g} km", textAnchor="middle", fontName="Helvetica", fontSize=6.5, fillColor=AZUL))
    texto = "© colaboradores do OpenStreetMap"
    d.add(Rect(0, 0, 112, 10, fillColor=colors.Color(1, 1, 1, 0.8), strokeColor=None))
    d.add(String(3, 2.5, texto, fontName="Helvetica", fontSize=6, fillColor=colors.HexColor("#333333")))
    return d


def _legenda(empresas: list[dict], st) -> Table | None:
    vistos: dict[str, str] = {}
    for e in empresas:
        if e.get("latitude") is not None:
            vistos.setdefault(e.get("situacao_nome") or "Sem situação", e.get("situacao_cor") or "#6b7280")
    if not vistos:
        return None
    cel = []
    for nome, cor in sorted(vistos.items()):
        dd = Drawing(8, 8)
        dd.add(Circle(4, 4, 3.5, fillColor=_cor(cor), strokeColor=colors.white))
        cel.append([dd, Paragraph(esc(nome), st["s"])])
    linhas = [sum(cel[i:i + 3], []) for i in range(0, len(cel), 3)]
    t = Table([l + [""] * (6 - len(l)) for l in linhas], colWidths=[12, LARGURA / 3 - 12] * 3)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                           ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _bloco_empresa(n: int, e: dict, st) -> list:
    nome = f"<b>{esc(e['nome'])}</b>" + (f"<br/>{esc(e['nome_fantasia'])}" if e.get("nome_fantasia") else "")
    contato = " · ".join(x for x in (esc(e["email"]) if e.get("email") else "", esc(fone_fmt(e.get("celular"))), esc(fone_fmt(e.get("fone")))) if x)
    col1 = Paragraph(nome + f"<br/>CNPJ: {esc(cnpj_fmt(e.get('cnpj'))) or '—'}" + (f"<br/>{contato}" if contato else ""), st["s"])
    rua = ", ".join(x for x in (e.get("rua"), e.get("numero"), e.get("complemento")) if x)
    col2 = Paragraph(f"{esc(rua) or '—'}<br/>{esc(' · '.join(x for x in (e.get('bairro'), e.get('cidade') and (e['cidade'] + (('/' + e['estado']) if e.get('estado') else '')), cep_fmt(e.get('cep'))) if x))}", st["s"])
    cats = ", ".join(c["nome"] for c in e.get("categorias", []))
    loc = GEO.get(e.get("geocode_status"), "")
    if e.get("latitude") is not None and e.get("geocode_precisao"):
        loc += f" ({PRECISAO.get(e['geocode_precisao'], e['geocode_precisao'])})"
    col3 = Paragraph(f"Situação: <b>{esc(e.get('situacao_nome') or '—')}</b><br/>Porte: {esc(e.get('porte_nome') or '—')}<br/>Tipo: {esc(e.get('tipo_nome') or '—')}"
                     + (f"<br/>Categorias: {esc(cats)}" if cats else "") + f"<br/><i>{esc(loc)}</i>", st["s"])
    u = e.get("ultima_interacao")
    col4 = Paragraph((f"<b>{esc(data_local(u['data_interacao']))}</b> · {esc(TIPOS.get(u['tipo'], u['tipo']))} · {esc(u['usuario'])}<br/>{esc(u['descricao'][:240] + ('…' if len(u['descricao']) > 240 else ''))}")
                     if u else "<i>Sem interações registradas.</i>", st["s"])
    return [Paragraph(str(n), st["s"]), col1, col2, col3, col4]


def gerar(*, titulo: str, criterios: list[tuple[str, str]], resumo: dict, empresas: list[dict], usuario: str, agora: str, base=None, aviso_mapa: str | None = None) -> bytes:
    st = _estilos()
    buf = io.BytesIO()

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(15 * mm, 9 * mm, limpar(f"Sindnorte · gerado em {agora} por {usuario} · documento com dados pessoais: uso interno"))
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Página {doc.page}")
        canvas.setStrokeColor(DOURADO)
        canvas.setLineWidth(1.2)
        canvas.line(15 * mm, A4[1] - 12 * mm, A4[0] - 15 * mm, A4[1] - 12 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=17 * mm, bottomMargin=15 * mm,
                            title=limpar(titulo), author="Sindnorte", subject="Relatório de empresas")
    el: list = _cabecalho(titulo, agora, usuario, st)
    crit = [[Paragraph("<b>Critérios da busca</b>", st["n"]), ""]] + [[Paragraph(esc(k), st["s"]), Paragraph(esc(v), st["s"])] for k, v in criterios]
    if not criterios:
        crit.append([Paragraph("Todas as empresas", st["s"]), Paragraph("Nenhum filtro aplicado.", st["s"])])
    t = Table(crit, colWidths=[38 * mm, LARGURA - 38 * mm])
    t.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d1de")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    el += [t, Spacer(1, 6)]
    el.append(Paragraph(f"<b>{resumo['total']}</b> empresa(s) encontrada(s) · <b>{resumo['com_localizacao']}</b> com localização no mapa · <b>{resumo['sem_localizacao']}</b> sem localização"
                        + (f" · {resumo['excluidos_sem_numero']} sem número identificável ficaram de fora do filtro par/ímpar" if resumo.get("excluidos_sem_numero") else ""), st["n"]))
    el.append(Paragraph("Mapa", st["h2"]))
    el.append(mapa(empresas, base=base))
    if aviso_mapa:
        el.append(Paragraph(esc(aviso_mapa), st["muted"]))
    lg = _legenda(empresas, st)
    if lg:
        el += [Spacer(1, 3), lg, Paragraph("Número ao lado do ponto = número da empresa na lista abaixo (mostrado quando há até 60 pontos). Ponto menor = localização aproximada; contorno laranja = para revisão.", st["muted"])]
    el.append(Paragraph("Empresas", st["h2"]))
    cab = [Paragraph(x, st["cab"]) for x in ("Nº", "Empresa / contato", "Endereço", "Classificação", "Última interação")]
    linhas = [cab] + [_bloco_empresa(n, e, st) for n, e in enumerate(empresas, 1)]
    tab = Table(linhas, colWidths=[8 * mm, 50 * mm, 43 * mm, 38 * mm, LARGURA - 139 * mm], repeatRows=1)
    tab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AZUL), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d1de")),
                             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fc")]),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    el.append(tab)
    doc.build(el, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()


def _tabela_resumo(titulo: str, linhas: list[tuple[str, int]], st, larg: float) -> Table:
    dados = [[Paragraph(f"<b>{esc(titulo)}</b>", st["cab"]), Paragraph("<b>Qtde</b>", st["cab"])]] + [[Paragraph(esc(k), st["s"]), Paragraph(str(v), st["s"])] for k, v in linhas]
    t = Table(dados, colWidths=[larg - 14 * mm, 14 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AZUL), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d1de")),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def gerar_atividades(*, titulo: str, criterios: list[tuple[str, str]], resumo: dict, atividades: list[dict], usuario: str, agora: str, truncado: bool) -> bytes:
    """Relatório por período, com base nas interações (atividades) registradas: resumo por tipo/responsável/dia + lista cronológica."""
    st = _estilos()
    buf = io.BytesIO()

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(15 * mm, 9 * mm, limpar(f"Sindnorte · gerado em {agora} por {usuario} · documento com dados pessoais: uso interno"))
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Página {doc.page}")
        canvas.setStrokeColor(DOURADO)
        canvas.setLineWidth(1.2)
        canvas.line(15 * mm, A4[1] - 12 * mm, A4[0] - 15 * mm, A4[1] - 12 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=17 * mm, bottomMargin=15 * mm,
                            title=limpar(titulo), author="Sindnorte", subject="Relatório de atividades")
    el: list = _cabecalho(titulo, agora, usuario, st)
    crit = [[Paragraph("<b>Critérios do relatório</b>", st["n"]), ""]] + [[Paragraph(esc(k), st["s"]), Paragraph(esc(v), st["s"])] for k, v in criterios]
    t = Table(crit, colWidths=[38 * mm, LARGURA - 38 * mm])
    t.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d1de")),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    el += [t, Spacer(1, 6),
           Paragraph(f"<b>{resumo['total']}</b> atividade(s) registrada(s) · <b>{resumo['empresas']}</b> empresa(s) atendida(s) · <b>{resumo['usuarios']}</b> responsável(is)", st["n"])]
    if resumo["total"]:
        meia = (LARGURA - 6 * mm) / 2
        por_tipo = [(TIPOS.get(k, k), v) for k, v in resumo["por_tipo"].items()]
        por_user = list(resumo["por_usuario"].items())
        por_dia = [(f"{k[8:10]}/{k[5:7]}/{k[:4]}", v) for k, v in resumo["por_dia"].items()]
        esq = [_tabela_resumo("Por tipo", por_tipo, st, meia), Spacer(1, 4), _tabela_resumo("Por responsável", por_user, st, meia)]
        dir_ = [_tabela_resumo("Por dia", por_dia[:60], st, meia)]
        el += [Spacer(1, 6), Table([[esq, dir_]], colWidths=[meia + 3 * mm, meia + 3 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])]
        if len(por_dia) > 60:
            el.append(Paragraph(f"Resumo por dia limitado aos primeiros 60 dias de {len(por_dia)}.", st["muted"]))
    el.append(Paragraph("Atividades", st["h2"]))
    if not atividades:
        el.append(Paragraph("Nenhuma atividade registrada neste período.", st["n"]))
    else:
        cab = [Paragraph(x, st["cab"]) for x in ("Data/hora", "Empresa", "Tipo / responsável", "Descrição")]
        linhas = [cab]
        for a in atividades:
            emp = f"<b>{esc(a['nome'])}</b><br/>CNPJ: {esc(cnpj_fmt(a.get('cnpj'))) or '—'}" + (f"<br/>{esc(' · '.join(x for x in (a.get('bairro'), a.get('cidade')) if x))}" if a.get("bairro") or a.get("cidade") else "")
            desc = esc(a["descricao"][:600] + ("…" if len(a["descricao"]) > 600 else ""))
            if a.get("observacoes"):
                desc += f"<br/><i>Obs.: {esc(a['observacoes'][:300])}</i>"
            if a.get("situacao_resultante"):
                desc += f"<br/>Situação resultante: <b>{esc(a['situacao_resultante'])}</b>"
            linhas.append([Paragraph(esc(data_local(a["data_interacao"])), st["s"]), Paragraph(emp, st["s"]),
                           Paragraph(f"{esc(TIPOS.get(a['tipo'], a['tipo']))}<br/>{esc(a['usuario'])}", st["s"]), Paragraph(desc, st["s"])])
        tab = Table(linhas, colWidths=[25 * mm, 52 * mm, 30 * mm, LARGURA - 107 * mm], repeatRows=1)
        tab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AZUL), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d1de")),
                                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fc")]),
                                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        el.append(tab)
        if truncado:
            el.append(Paragraph("Lista limitada às primeiras atividades do período; refine o período ou os filtros para ver as demais.", st["muted"]))
    doc.build(el, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()
