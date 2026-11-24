"""Geocodifica as empresas pendentes pelo Nominatim (OpenStreetMap), 1 consulta por segundo, e GRAVA latitude/longitude no banco.
Pode parar (Ctrl+C) e continuar depois: cada resultado é gravado na hora.
  python -m scripts.geocode --amostra 15 --dry-run   # só mostra o que seria encontrado (NÃO grava): confira a qualidade primeiro
  python -m scripts.geocode                          # processa todas as pendentes
  python -m scripts.geocode --retentar-falhas        # tenta de novo as 'não encontradas'
  python -m scripts.geocode --testar                 # 1 consulta de teste: mostra se o serviço responde (ou está bloqueando)
  python -m scripts.geocode --diagnostico            # por que as empresas não foram mapeadas (não usa a internet)
  python -m scripts.geocode --refazer nao_encontradas|revisao|todas   # devolve à fila e processa de novo
Antes: defina GEOCODER_EMAIL no .env (a política do Nominatim exige um contato)."""
import argparse
import os
import sys
import time

from app.config import get_settings
from app.db import get_engine
from app.modules.geocoding import service as svc
from app.modules.geocoding.client import GeocoderConfigInvalida, GeocoderIndisponivel
from app.schema import init_db

ROTULO = {"localizado": "localizada", "revisao": "precisa de revisão", "nao_encontrado": "não encontrada",
          "pulada": "pulada", "indisponivel": "serviço indisponível"}


def main(argv=None, geo=None, saida=print) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0, help="máximo de empresas (0 = todas)")
    ap.add_argument("--amostra", type=int, default=0, help="só N empresas (use com --dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="mostra os resultados sem gravar nada")
    ap.add_argument("--retentar-falhas", action="store_true")
    ap.add_argument("--detalhe", action="store_true", help="com --dry-run: mostra cada busca tentada")
    ap.add_argument("--testar", action="store_true")
    ap.add_argument("--diagnostico", action="store_true")
    ap.add_argument("--refazer", choices=["nao_encontradas", "revisao", "todas"])
    a = ap.parse_args(argv)
    init_db(get_engine())
    if a.diagnostico:
        with get_engine().connect() as db:
            return _diagnostico(db, saida)
    if not get_settings().geocoder_email:
        saida("AVISO: defina GEOCODER_EMAIL no .env — a política do Nominatim exige um contato no User-Agent.")
    try:
        geo = geo or svc.criar_geocoder()
        if a.testar:
            r = svc.testar_conexao(geo)
            saida(("OK: " if r["ok"] else "FALHOU: ") + r["mensagem"] + f" ({r['ms']} ms)")
            return 0 if r["ok"] else 1
        with get_engine().connect() as db:
            if a.refazer:
                saida(f"{svc.reprocessar(db, a.refazer)} empresas devolvidas à fila.")
            total = a.amostra or a.limite or 10**9
            if a.dry_run:
                return _dry_run(db, geo, a.amostra or 15, a.retentar_falhas, saida, a.detalhe)
            falta = len(svc.pendentes(db, 10**6, 0, a.retentar_falhas))
            saida(f"{falta} empresas para geocodificar (≈ {falta * 1.2 / 60:.0f} min). Ctrl+C para pausar; dá para continuar depois.")
            feitos, ultimo, t0, cont = 0, 0, time.time(), {}
            holder = f"cli-{os.getpid()}"
            while feitos < total:
                r = svc.processar_lote(db, geo, min(25, total - feitos), holder, a.retentar_falhas, apos_id=ultimo)
                if r.get("ocupado"):
                    saida("Já existe outra geocodificação em andamento — provavelmente a automática do servidor, e então você não precisa rodar este script. (Se não for, aguarde 2 minutos.)")
                    return 2
                n = sum(r[k] for k in ("localizado", "revisao", "nao_encontrado", "pulada", "indisponivel"))
                for k in ("localizado", "revisao", "nao_encontrado", "indisponivel"):
                    cont[k] = cont.get(k, 0) + r[k]
                feitos, ultimo = feitos + n, r["ultimo_id"] or ultimo
                saida(f"{feitos} feitas · localizadas {cont.get('localizado', 0)} · revisão {cont.get('revisao', 0)} · "
                      f"não encontradas {cont.get('nao_encontrado', 0)} · {time.time() - t0:.0f}s")
                if r.get("interrompido"):
                    saida("O serviço de geocodificação parou de responder. Tente novamente mais tarde (nada se perdeu).")
                    return 1
                if n == 0:
                    break
            saida("Concluído.")
            return 0
    except GeocoderConfigInvalida as e:
        saida(f"Configuração inválida: {e}")
        return 1
    except KeyboardInterrupt:
        saida("\nPausado. Rode o comando de novo para continuar de onde parou.")
        return 130


def _dry_run(db, geo, n, retentar, saida, detalhe=False) -> int:
    ids = svc.pendentes(db, n, 0, retentar)
    saida(f"Simulação com {len(ids)} empresas (nada será gravado):")
    cont = {"localizado": 0, "revisao": 0, "nao_encontrado": 0}
    for eid in ids:
        e = svc.carregar(db, eid)
        try:
            achou, tent = svc.buscar_melhor(None, geo, e)
        except GeocoderIndisponivel as ex:
            saida(f"  #{eid}: serviço indisponível ({ex})")
            return 1
        end = f"{e['rua'] or '—'}, {e['numero'] or 's/n'} · {e['cidade'] or '—'}"
        if achou:
            status, obs = svc.classificar(achou, e)
            cont[status] += 1
            rot = {"localizado": "LOCALIZADA", "revisao": "REVISÃO"}[status]
            saida(f"  #{eid}: {end} → {rot} (nível: {achou['precisao']}) {achou['lat']:.5f}, {achou['lon']:.5f}" + (f" — {obs}" if obs else ""))
        else:
            cont["nao_encontrado"] += 1
            saida(f"  #{eid}: {end} → NÃO ENCONTRADA")
        if detalhe:
            for t in tent:
                saida(f"       {'✓' if t['achou'] else '✗'} [{t['nivel']}] {t['busca']}")
    total = max(1, len(ids))
    saida(f"\nResumo: localizadas {cont['localizado']} ({100 * cont['localizado'] // total}%) · revisão {cont['revisao']} · não encontradas {cont['nao_encontrado']}")
    return 0


def _diagnostico(db, saida) -> int:
    d = svc.diagnostico(db)
    r = d["resumo"]
    saida(f"Total: {r['total']} | localizadas {r['localizado']} | para revisão {r['revisao']} | não encontradas {r['nao_encontrado']} | pendentes {r['pendente']}")
    saida(f"Geocodificação automática: {'ATIVA' if r['automatica'] else 'desligada'} (neste processo)")
    saida(f"Sem rua ou sem cidade no cadastro (impossível mapear): {d['sem_rua_ou_cidade']}")
    saida(f"Precisão dos pontos já gravados: {d['por_precisao']}")
    saida("Motivos mais comuns:")
    for m in d["motivos"]:
        saida(f"  {m['quantidade']:5d}  [{m['status']}] {m['motivo']}")
    if d["exemplos_nao_encontradas"]:
        saida("Exemplos de não encontradas:")
        for x in d["exemplos_nao_encontradas"]:
            saida(f"  #{x['id']}: {x['rua']}, {x['numero'] or 's/n'} · {x['bairro'] or '—'} · {x['cidade']} · CEP {x['cep']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
