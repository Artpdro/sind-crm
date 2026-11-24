"""Geocodificação AUTOMÁTICA em segundo plano: toda empresa nova (cadastro, importação) ou com endereço alterado entra na fila
e é mapeada sozinha, a 1 consulta por segundo. É uma thread do servidor; a trava (geocode_lease) garante que só um
geocodificador rode por vez (inclusive se houver vários processos ou o script de terminal)."""
import logging
import os
import threading

from app.config import get_settings
from app.db import get_engine
from app.modules.geocoding import service as svc
from app.modules.geocoding.client import GeocoderConfigInvalida

log = logging.getLogger("geocodificacao")
LOTE = 10
ESPERA_SERVICO_FORA = 300.0   # serviço externo indisponível: espera 5 min antes de tentar de novo
ESPERA_ERRO = 60.0


class Automatico:
    def __init__(self):
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._geo = None
        self._criar = None
        self.intervalo = 15.0
        self.espera_extra = 0.0
        self.holder = f"auto-{os.getpid()}"

    @property
    def ativo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self, criar_geocoder=None, intervalo: float | None = None) -> None:
        if self.ativo:
            return
        self._criar = criar_geocoder or svc.criar_geocoder
        self.intervalo = intervalo if intervalo is not None else get_settings().geocoder_auto_intervalo
        self._parar.clear()
        self._acordar.set()  # já começa verificando a fila (empresas importadas antes, por exemplo)
        self._geo = None
        self._thread = threading.Thread(target=self._loop, name="geocodificacao-automatica", daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        """Chamado depois de gravar uma empresa nova/alterada. Sem custo se o serviço automático não estiver ligado."""
        self._acordar.set()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()
        if self._thread:
            self._thread.join(timeout=15)
        self._thread = None
        self._geo = None

    def rodada(self) -> int:
        """Processa até LOTE empresas pendentes. Retorna quantas foram tratadas (0 = fila vazia, ocupado ou serviço fora)."""
        if self._geo is None:
            self._geo = (self._criar or svc.criar_geocoder)()
        with get_engine().connect() as db:
            r = svc.processar_lote(db, self._geo, LOTE, holder=self.holder)
        if r.get("ocupado"):
            return 0
        if r.get("interrompido"):
            self.espera_extra = ESPERA_SERVICO_FORA
            log.warning("serviço de geocodificação indisponível; nova tentativa em %.0f s", ESPERA_SERVICO_FORA)
        return sum(r.get(k, 0) for k in ("localizado", "revisao", "nao_encontrado", "pulada"))

    def _loop(self) -> None:
        while not self._parar.is_set():
            n, espera = 0, None
            try:
                n = self.rodada()
            except GeocoderConfigInvalida:
                log.error("GEOCODER_URL inválida: geocodificação automática em pausa")
                espera = ESPERA_ERRO * 5
            except Exception:  # nosec B110 - a thread nunca pode morrer; o erro vai para o log do servidor
                log.exception("falha inesperada na geocodificação automática")
                espera = ESPERA_ERRO
            if self._parar.is_set():
                break
            if espera or self.espera_extra:  # recuo: ignora "acordar" para não martelar um serviço que caiu
                self._parar.wait(espera or self.espera_extra)
                self.espera_extra = 0.0
            elif n == 0:
                self._acordar.wait(self.intervalo)
                self._acordar.clear()


auto = Automatico()
