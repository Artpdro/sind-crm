"""Datas em UTC como texto 'AAAA-MM-DD HH:MM:SS.mmm' (mesmo formato do strftime do SQLite).
Nesse formato a comparação de texto equivale à comparação de datas."""
from datetime import datetime, timedelta, timezone


def now(**delta: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(**delta)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
