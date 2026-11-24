import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import get_engine
from app.modules.auth.router import router as auth_router
from app.modules.cadastros.router import router as cadastros_router
from app.modules.companies.router import router as empresas_router
from app.modules.geocoding.auto import auto
from app.modules.geocoding.router import router as geocoding_router
from app.modules.importacao.router import router as importacao_router
from app.modules.mapa.router import router as mapa_router
from app.modules.relatorios.router import router as relatorios_router
from app.modules.dashboard.router import router as dashboard_router
from app.schema import init_db
from app.modules.users.router import router as users_router

log = logging.getLogger("app")
settings = get_settings()

class BodyTooLarge(HTTPException):
    """HTTPException de propósito: o FastAPI converte qualquer outra exceção durante a leitura do corpo em 400."""
    def __init__(self):
        super().__init__(status_code=413, detail="Requisição grande demais.")


class BodyLimit:
    """Limite de tamanho do corpo para escritas, inclusive quando o cliente envia em partes
    (sem Content-Length). Upload de planilha tem limite próprio; o resto, o limite de JSON."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        s = get_settings()
        limit = s.max_upload_bytes + 65536 if scope["path"] == "/api/importacoes" else s.max_json_body_bytes
        cl = dict(scope["headers"]).get(b"content-length")
        if cl and cl.isdigit() and int(cl) > limit:
            return await JSONResponse({"detail": "Requisição grande demais."}, status_code=413)(scope, receive, send)
        total = 0

        async def limitado():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:
                    raise BodyTooLarge()
            return msg

        await self.app(scope, limitado, send)


@asynccontextmanager
async def lifespan(_app):
    init_db(get_engine())  # aplica migrações pendentes (não apaga dados)
    if settings.geocoder_auto:
        auto.iniciar()  # empresas novas/alteradas são mapeadas sozinhas
    yield
    auto.parar()


app = FastAPI(
    lifespan=lifespan,
    title="Sindnorte — Gestão de Empresas",
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

# CORS restritivo: só as origens listadas; nunca "*" com credenciais.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    max_age=600,
)

app.add_middleware(BodyLimit)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def guards(request: Request, call_next):
    if request.method in UNSAFE:
        # 1) Origin: se o navegador informou, precisa ser o próprio host ou uma origem permitida.
        origin = request.headers.get("origin")
        if origin:
            same_host = urlparse(origin).netloc == request.headers.get("host")
            if not same_host and origin.rstrip("/") not in settings.origins:
                return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"  # API só devolve JSON
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path.startswith("/api"):  # padrão seguro: NENHUMA resposta da API (dados pessoais) fica em cache; rota nova já nasce protegida
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Não devolve o valor enviado (evita refletir senha/entrada); só campo e motivo.
    errs = [{"campo": ".".join(str(p) for p in e["loc"][1:]), "erro": e["msg"]} for e in exc.errors()]
    return JSONResponse({"detail": "Dados inválidos.", "erros": errs}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("erro não tratado em %s %s", request.method, request.url.path)  # detalhe só no log do servidor
    return JSONResponse({"detail": "Erro interno. Tente novamente."}, status_code=500)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(cadastros_router)
app.include_router(empresas_router)
app.include_router(importacao_router)
app.include_router(geocoding_router)
app.include_router(mapa_router)
app.include_router(relatorios_router)
app.include_router(dashboard_router)
