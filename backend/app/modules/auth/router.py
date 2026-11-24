import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import Connection, text

from app import timeutil
from app.config import get_settings
from app.db import get_db
from app.modules.audit import service as audit
from app.modules.auth import mailer
from app.modules.auth.schemas import ChangePasswordIn, LoginIn, ResetConfirmIn, ResetRequestIn
from app.security import ratelimit, sessions
from app.security.net import client_ip
from app.security.passwords import (DUMMY_HASH, hash_password, needs_rehash, validate_password_policy,
                                    verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILS = 5
LOCK_MINUTES = 15
GENERIC_LOGIN_ERROR = "Usuário ou senha inválidos."


def _too_many() -> HTTPException:
    return HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.", headers={"Retry-After": "300"})


def _user_payload(u) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "full_name": u.get("full_name"), "must_change_password": bool(u["must_change_password"])}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Connection = Depends(get_db)):
    ip = client_ip(request)
    uname = body.username.lower()
    # Limites independentes da existência do usuário => não vazam se a conta existe.
    if not ratelimit.allow(db, "login_ip", ip, 20, 300) or not ratelimit.allow(db, "login_user", uname, 8, 900):
        audit.record(db, request, "login_bloqueado_rate_limit", username=uname)
        db.commit()
        raise _too_many()

    user = db.execute(text("SELECT * FROM users WHERE username = :u"), {"u": body.username}).mappings().first()
    locked = bool(user and user["locked_until"] and user["locked_until"] > timeutil.now())
    # Sempre executa uma verificação Argon2 (mesmo custo com usuário inexistente/bloqueado).
    ok = verify_password(user["password_hash"] if user else DUMMY_HASH, body.password)

    if not user or not user["is_active"] or locked or not ok:
        if user and user["is_active"] and not locked:
            fails = user["failed_attempts"] + 1
            lock = timeutil.now(minutes=LOCK_MINUTES) if fails >= MAX_FAILS else user["locked_until"]
            db.execute(text("UPDATE users SET failed_attempts = :f, locked_until = :l WHERE id = :i"),
                       {"f": fails, "l": lock, "i": user["id"]})
        audit.record(db, request, "login_falha", user_id=user["id"] if user else None, username=uname)
        db.commit()
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)  # mesma resposta em todos os casos

    # Session fixation: qualquer cookie de sessão apresentado é revogado e um token novo é emitido.
    old = sessions.resolve_session(request, db)
    if old:
        sessions.revoke_session(db, old.session_id)
    if needs_rehash(user["password_hash"]):
        db.execute(text("UPDATE users SET password_hash = :h WHERE id = :i"),
                   {"h": hash_password(body.password), "i": user["id"]})
    db.execute(text("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = :now WHERE id = :i"),
               {"i": user["id"], "now": timeutil.now()})
    token, csrf = sessions.create_session(db, request, user["id"])
    audit.record(db, request, "login", user_id=user["id"], username=user["username"])
    db.commit()
    sessions.set_cookie(response, token)
    response.headers["Cache-Control"] = "no-store"
    return {"user": _user_payload(dict(user)), "csrf_token": csrf}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Connection = Depends(get_db)):
    user = sessions.resolve_session(request, db)
    if user:
        sessions.check_csrf(request, user)
        sessions.revoke_session(db, user.session_id)
        audit.record(db, request, "logout", user_id=user.user_id, username=user.username)
        db.commit()
    sessions.clear_cookie(response)
    response.status_code = 204
    return response


@router.get("/me")
def me(request: Request, user=Depends(sessions.current_user_allow_pending)):
    return {"user": {"id": user.user_id, "username": user.username, "role": user.role,
                     "must_change_password": user.must_change_password},
            "csrf_token": user.csrf_token}


@router.post("/refresh")
def refresh(request: Request, response: Response, user=Depends(sessions.current_user_allow_pending),
            db: Connection = Depends(get_db)):
    """Renovação: rotaciona token e CSRF. O limite ABSOLUTO da sessão original é mantido
    (um token roubado não vira sessão eterna)."""
    s = get_settings()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(
        text("INSERT INTO sessions (user_id, token_hash, csrf_token, ip, user_agent, created_at, last_seen_at, expires_at) "
             "SELECT user_id, :h, :c, :ip, user_agent, created_at, :now, expires_at FROM sessions WHERE id = :i"),
        {"h": hashlib.sha256(token.encode()).hexdigest(), "c": csrf, "ip": client_ip(request), "i": user.session_id,
         "now": timeutil.now()},
    )
    sessions.revoke_session(db, user.session_id)
    db.commit()
    sessions.set_cookie(response, token)
    return {"csrf_token": csrf, "idle_minutes": s.session_idle_minutes}


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordIn, request: Request, user=Depends(sessions.current_user_allow_pending),
                    db: Connection = Depends(get_db)):
    if not ratelimit.allow(db, "change_pw", str(user.user_id), 10, 900):
        raise _too_many()
    row = db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": user.user_id}).mappings().one()
    if not verify_password(row["password_hash"], body.current_password):
        audit.record(db, request, "troca_senha_falha", user_id=user.user_id, username=user.username)
        db.commit()
        raise HTTPException(status_code=400, detail="Senha atual incorreta.")
    err = validate_password_policy(body.new_password, user.username)
    if err:
        raise HTTPException(status_code=422, detail=err)
    db.execute(text("UPDATE users SET password_hash = :h, must_change_password = 0 WHERE id = :i"),
               {"h": hash_password(body.new_password), "i": user.user_id})
    sessions.revoke_all_sessions(db, user.user_id, except_session_id=user.session_id)
    audit.record(db, request, "troca_senha", user_id=user.user_id, username=user.username)
    db.commit()
    return Response(status_code=204)


@router.post("/password-reset/request", status_code=202)
def reset_request(body: ResetRequestIn, request: Request, db: Connection = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request)
    if not ratelimit.allow(db, "reset_ip", ip, 5, 900) or not ratelimit.allow(db, "reset_email", body.email.lower(), 3, 900):
        raise _too_many()
    user = db.execute(text("SELECT id, username, email FROM users WHERE email = :e AND is_active = 1"),
                      {"e": body.email}).mappings().first()
    if user:
        token = secrets.token_urlsafe(32)
        db.execute(
            text("INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (:u, :h, :exp)"),
            {"u": user["id"], "h": hashlib.sha256(token.encode()).hexdigest(),
             "exp": timeutil.now(minutes=s.reset_token_minutes)},
        )
        audit.record(db, request, "reset_senha_solicitado", user_id=user["id"], username=user["username"])
        db.commit()
        mailer.send_reset_email(user["email"], f"{s.frontend_base_url}/redefinir-senha#token={token}")
    # Resposta idêntica exista o e-mail ou não (anti-enumeração).
    return {"detail": "Se o e-mail estiver cadastrado, enviaremos as instruções."}


@router.post("/password-reset/confirm", status_code=204)
def reset_confirm(body: ResetConfirmIn, request: Request, db: Connection = Depends(get_db)):
    if not ratelimit.allow(db, "reset_confirm_ip", client_ip(request), 10, 900):
        raise _too_many()
    h = hashlib.sha256(body.token.encode()).hexdigest()
    # UPDATE ... RETURNING garante uso único mesmo com requisições simultâneas (sem race).
    row = db.execute(
        text("UPDATE password_reset_tokens SET used_at = :now WHERE token_hash = :h AND used_at IS NULL "
             "AND expires_at > :now RETURNING user_id"), {"h": h, "now": timeutil.now()},
    ).mappings().first()
    if not row:
        db.commit()
        raise HTTPException(status_code=400, detail="Link inválido ou expirado.")
    user = db.execute(text("SELECT id, username FROM users WHERE id = :i AND is_active = 1"), {"i": row["user_id"]}).mappings().first()
    err = validate_password_policy(body.new_password, user["username"] if user else "")
    if not user or err:
        db.rollback()  # não consome o token se a senha for rejeitada
        raise HTTPException(status_code=422, detail=err or "Link inválido ou expirado.")
    db.execute(text("UPDATE users SET password_hash = :h, must_change_password = 0, failed_attempts = 0, "
                    "locked_until = NULL WHERE id = :i"), {"h": hash_password(body.new_password), "i": user["id"]})
    sessions.revoke_all_sessions(db, user["id"])
    audit.record(db, request, "reset_senha_concluido", user_id=user["id"], username=user["username"])
    db.commit()
    return Response(status_code=204)
