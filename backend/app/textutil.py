import math
import unicodedata


def sem_acento(valor) -> str | None:
    """'Cuiabá  Centro' -> 'cuiaba centro' (minúsculas, sem acentos, espaços normalizados)."""
    if valor is None:
        return None
    t = unicodedata.normalize("NFKD", str(valor))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(t.split())


def distancia_m(lat1, lon1, lat2, lon2) -> float | None:
    """Distância em metros (fórmula de Haversine)."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))
