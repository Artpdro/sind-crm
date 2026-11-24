from functools import lru_cache

import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # development | test | production
    database_url: str = "sqlite:///./data/sindnorte.db"  # arquivo local, sem servidor
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cookie_secure: bool = True  # falha fechado: só desliga explicitamente em dev
    trust_proxy_headers: bool = False
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    reset_token_minutes: int = 30
    max_json_body_bytes: int = 1_000_000
    geocoder_url: str = "https://nominatim.openstreetmap.org"
    geocoder_email: str = ""  # contato exigido pela política de uso do Nominatim (vai no User-Agent)
    geocoder_min_interval: float = 1.1  # segundos entre consultas (a política permite no máximo 1 por segundo)
    geocoder_allow_local: bool = False  # true só para um Nominatim próprio em localhost
    geocoder_auto: bool = True  # empresa nova/alterada é geocodificada sozinha, em segundo plano (desligue com GEOCODER_AUTO=false)
    geocoder_auto_intervalo: float = 15.0  # segundos entre verificações quando não há nada na fila
    geocoder_pular_numero_raro: bool = True  # pula a busca 'com número' se ela quase nunca acha (o OSM do Brasil raramente tem o número)
    cnefe_ativo: bool = True  # usa a base do IBGE (CNEFE) quando ela foi importada: localização exata por número
    cnefe_db: str = "./data/cnefe.db"
    receita_ativo: bool = True  # consulta da situação cadastral (ativa/inativa/inapta) na Receita Federal
    receita_url: str = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"  # serviço público que espelha a base da Receita ({cnpj} = 14 dígitos)
    receita_timeout: float = 8.0
    receita_cache_horas: int = 24
    max_marcadores: int = 10000
    pdf_mapa_ruas: bool = True  # mapa do PDF com as ruas do OpenStreetMap (precisa de internet ao gerar; sem ela cai no mapa esquemático)
    tiles_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tiles_dir: str = "./data/tiles"
    tiles_cache_dias: int = 14
    max_pdf_empresas: int = 1500  # PDF das empresas da busca
    max_pdf_atividades: int = 3000  # PDF do relatório de atividades
    max_upload_bytes: int = 5 * 1024 * 1024  # planilha .xlsx (a real tem menos de 1 MB)
    frontend_base_url: str = "http://localhost:5173"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @model_validator(mode="before")
    @classmethod
    def _ignora_comentarios_no_env(cls, dados):
        """`CHAVE=   # comentário` num .env vira o texto do comentário como VALOR. Valor que começa com '#' é tratado como vazio."""
        if isinstance(dados, dict):
            return {k: v for k, v in dados.items() if not (isinstance(v, str) and v.strip().startswith("#"))}
        return dados

    @field_validator("geocoder_email")
    @classmethod
    def _email_do_geocodificador(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"[^@\s#]+@[^@\s#]+\.[^@\s#]+", v):
            raise ValueError("GEOCODER_EMAIL deve ser um e-mail válido (ex.: ti@sindnorte.org.br) ou ficar vazio.")
        return v

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _prod_guards(self):
        if self.is_production:
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE deve ser true em produção")
            if "*" in self.origins:
                raise ValueError("ALLOWED_ORIGINS não pode conter '*'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
