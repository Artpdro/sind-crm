import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # Argon2id com parâmetros padrão seguros do argon2-cffi
# Hash "isca" para igualar o tempo de resposta quando o usuário não existe (anti-enumeração por timing)
DUMMY_HASH = _ph.hash("dummy-password-for-timing-equalization")

_COMMON = {
    "1234567890", "12345678910", "password12", "password123", "senha12345", "senha123456",
    "qwertyuiop", "1q2w3e4r5t", "admin12345", "sindnorte123", "abcdefghij", "0123456789",
}


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


def validate_password_policy(pw: str, username: str = "") -> str | None:
    """Retorna mensagem de erro (pt-BR) ou None se a senha é aceitável."""
    if len(pw) < 10:
        return "A senha deve ter pelo menos 10 caracteres."
    if len(pw) > 128:
        return "A senha deve ter no máximo 128 caracteres."
    if pw.lower() in _COMMON or len(set(pw)) < 4:
        return "Senha muito comum ou previsível."
    if username and username.lower() in pw.lower():
        return "A senha não pode conter o nome de usuário."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"[0-9\W_]", pw)):
        return "Use letras e ao menos um número ou símbolo."
    return None
