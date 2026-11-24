"""Importa o CNEFE do IBGE (endereços com número e coordenada) para localização EXATA no mapa.
  python -m scripts.cnefe importar caminho/2610707_PAULISTA.zip            # um município por arquivo (.zip ou .csv)
  python -m scripts.cnefe importar a.zip b.zip c.zip                        # vários de uma vez
  python -m scripts.cnefe status                                           # o que já foi importado
  python -m scripts.cnefe aplicar                                          # refaz a localização das empresas com a base nova
Depois de importar, rode 'aplicar' (ou o botão 'Refazer todas' em Localização) para as empresas usarem a base."""
import argparse
import sys

from app.db import get_engine
from app.modules.geocoding import cnefe, service as svc
from app.schema import init_db


def main(argv=None, saida=print) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("importar")
    i.add_argument("arquivos", nargs="+")
    i.add_argument("--nome", help="nome do município (só se não for reconhecido)")
    sub.add_parser("status")
    ap_ = sub.add_parser("aplicar")
    ap_.add_argument("--incluir-manuais", action="store_true", help="(ignorado) correções manuais nunca são refeitas")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        e = cnefe.estado()
        if not e["municipios"]:
            saida("Nenhum município importado ainda.")
            return 1
        for m in e["municipios"]:
            saida(f"{m['nome']} ({m['cod']}): {m['pontos']} endereços")
        return 0
    if a.cmd == "importar":
        for arq in a.arquivos:
            try:
                saida(f"Importando {arq} …")
                r = cnefe.importar(arq, a.nome if len(a.arquivos) == 1 else None, saida)
                saida(f"  OK: {r['nome']} — {r['pontos']} endereços ({r['descartadas']} descartados sem rua/número/coordenada).")
            except (cnefe.CnefeInvalido, OSError) as ex:
                saida(f"  ERRO: {ex}")
                return 1
        saida("Agora rode:  python -m scripts.cnefe aplicar")
        return 0
    init_db(get_engine())
    with get_engine().connect() as db:
        n = svc.reprocessar(db, "todas")
    saida(f"{n} empresas devolvidas à fila; o servidor as refaz em segundo plano (ou rode: python -m scripts.geocode). Localizações manuais são mantidas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
