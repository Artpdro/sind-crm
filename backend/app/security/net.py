from fastapi import Request

from app.config import get_settings


def client_ip(request: Request) -> str:
    """IP do cliente. Só confia em cabeçalhos de proxy quando TRUST_PROXY_HEADERS=true
    (ex.: Vercel sobrescreve X-Forwarded-For). Caso contrário, usa o peer TCP."""
    if get_settings().trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]
