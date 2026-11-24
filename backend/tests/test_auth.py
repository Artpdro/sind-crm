import time

from app import timeutil

from sqlalchemy import text

from tests.conftest import ORIGIN, PASSWORD


def _login(client, u="ana", p=PASSWORD, headers=ORIGIN):
    return client.post("/api/auth/login", json={"username": u, "password": p}, headers=headers)


def test_login_ok_e_flags_do_cookie(client, make_user):
    make_user()
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "ana" and PASSWORD not in str(body) and body["csrf_token"]
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "path=/" in sc
    assert client.get("/api/auth/me").status_code == 200


def test_cookie_secure_e_host_prefix_quando_habilitado(monkeypatch):
    from app.config import get_settings
    from app.security import sessions
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    assert sessions.cookie_name() == "__Host-session"


def test_sem_autenticacao_401(client):
    for path in ["/api/auth/me", "/api/users"]:
        assert client.get(path).status_code == 401


def test_credenciais_erradas_e_usuario_inexistente_indistinguiveis(client, make_user):
    make_user()
    a = _login(client, "ana", "errada-errada-1")
    b = _login(client, "naoexiste", "errada-errada-1")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_tempo_similar_usuario_existente_e_inexistente(client, make_user):
    make_user()
    def t(u):
        s = time.perf_counter(); _login(client, u, "errada-errada-1"); return time.perf_counter() - s
    real = min(t("ana") for _ in range(3))
    fake = min(t("fantasma%d" % i) for i in range(3))
    assert abs(real - fake) < 0.5 * max(real, fake) + 0.05  # ambos pagam o custo do Argon2


def test_brute_force_bloqueia_conta_e_resposta_nao_revela(client, make_user):
    make_user()
    for _ in range(5):
        assert _login(client, "ana", "errada-errada-1").status_code == 401
    r = _login(client)  # senha CORRETA, mas conta bloqueada
    assert r.status_code == 401 and r.json()["detail"] == "Usuário ou senha inválidos."


def test_rate_limit_por_usuario_429(client, make_user):
    make_user()
    codes = [_login(client, "ana", "errada-errada-1").status_code for _ in range(10)]
    assert 429 in codes


def test_rate_limit_por_ip_429(client):
    codes = [_login(client, f"u{i}", "x-x-x-x-x-x-x-1").status_code for i in range(25)]
    assert codes[:20].count(401) == 20 and 429 in codes[20:]


def test_usuario_inativo_nao_loga(client, make_user):
    make_user(active=False)
    assert _login(client).status_code == 401


def test_logout_invalida_sessao(login):
    client, h = login()
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_logout_exige_csrf(login):
    client, h = login()
    assert client.post("/api/auth/logout", headers=ORIGIN).status_code == 403
    assert client.get("/api/auth/me").status_code == 200  # ainda logado


def test_token_invalido_e_adulterado(client, make_user):
    make_user()
    _login(client)
    good = client.cookies.get("session")
    for bad in ["lixo", good[:-3] + "AAA", "", "A" * 500, "' OR 1=1 --"]:
        client.cookies.clear()
        client.cookies.set("session", bad)
        assert client.get("/api/auth/me").status_code == 401


def test_sessao_expirada_absoluta(login, owner_engine):
    client, _ = login()
    with owner_engine.begin() as c:
        c.execute(text("UPDATE sessions SET expires_at = :t"), {"t": timeutil.now(seconds=-1)})
    assert client.get("/api/auth/me").status_code == 401


def test_sessao_expirada_por_inatividade(login, owner_engine):
    client, _ = login()
    with owner_engine.begin() as c:
        c.execute(text("UPDATE sessions SET last_seen_at = :t"), {"t": timeutil.now(minutes=-31)})
    assert client.get("/api/auth/me").status_code == 401


def test_session_fixation_token_e_renovado_no_login(client, make_user):
    make_user()
    _login(client)
    antigo = client.cookies.get("session")
    r = _login(client)  # reloga apresentando o cookie antigo
    novo = client.cookies.get("session")
    assert r.status_code == 200 and novo != antigo
    client.cookies.clear(); client.cookies.set("session", antigo)
    assert client.get("/api/auth/me").status_code == 401  # o antigo foi revogado


def test_fixation_cookie_forjado_pelo_atacante_nao_e_adotado(client, make_user):
    make_user()
    forjado = "token-que-o-atacante-escolheu-123456789"
    client.cookies.set("session", forjado, domain="localhost")
    r = _login(client)
    emitido = r.headers["set-cookie"].split("session=")[1].split(";")[0]
    assert r.status_code == 200 and emitido != forjado and len(emitido) >= 40
    # o token forjado continua sem valer nada
    client.cookies.clear(); client.cookies.set("session", forjado, domain="localhost")
    assert client.get("/api/auth/me").status_code == 401


def test_token_de_sessao_nao_fica_em_texto_no_banco(login, owner_engine):
    client, _ = login()
    tok = client.cookies.get("session")
    with owner_engine.begin() as c:
        rows = c.execute(text("SELECT token_hash FROM sessions")).scalars().all()
    assert tok not in rows and all(len(r) == 64 for r in rows)


def test_refresh_rotaciona_token_e_mantem_limite_absoluto(login, owner_engine):
    client, h = login()
    antigo = client.cookies.get("session")
    with owner_engine.begin() as c:
        exp_antes = c.execute(text("SELECT expires_at FROM sessions ORDER BY id LIMIT 1")).scalar_one()
    r = client.post("/api/auth/refresh", headers=h)
    assert r.status_code == 200 and client.cookies.get("session") != antigo
    with owner_engine.begin() as c:
        exp_depois = c.execute(text("SELECT expires_at FROM sessions WHERE revoked_at IS NULL")).scalar_one()
    assert exp_antes == exp_depois
    client.cookies.clear(); client.cookies.set("session", antigo)
    assert client.get("/api/auth/me").status_code == 401


def test_senha_nao_e_armazenada_em_texto(login, owner_engine):
    login()
    with owner_engine.begin() as c:
        h = c.execute(text("SELECT password_hash FROM users")).scalar_one()
    assert PASSWORD not in h and h.startswith("$argon2id$")


# ── CSRF / Origin ──
def test_csrf_ausente_e_invalido_403(login):
    client, h = login()
    body = {"current_password": PASSWORD, "new_password": "Outra-Senha-456"}
    assert client.post("/api/auth/change-password", json=body, headers=ORIGIN).status_code == 403
    bad = {**ORIGIN, "X-CSRF-Token": "errado"}
    assert client.post("/api/auth/change-password", json=body, headers=bad).status_code == 403


def test_origin_de_terceiro_bloqueado_ate_no_login(client, make_user):
    make_user()
    r = _login(client, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cors_nao_libera_origem_nao_autorizada(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ── Trocar / recuperar senha ──
def test_troca_de_senha_revoga_outras_sessoes(client, make_user):
    make_user()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, base_url="http://localhost:5173") as outro:
        _login(outro)
        r = _login(client)
        h = {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}
        ok = client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Nova-Senha-789!"}, headers=h)
        assert ok.status_code == 204
        assert outro.get("/api/auth/me").status_code == 401
        assert client.get("/api/auth/me").status_code == 200


def test_troca_exige_senha_atual_e_politica(login):
    client, h = login()
    r = client.post("/api/auth/change-password", json={"current_password": "errada", "new_password": "Nova-Senha-789!"}, headers=h)
    assert r.status_code == 400
    for fraca in ["curta1", "aaaaaaaaaaaa", "password123", "ana-ana-ana-1"]:
        r = client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": fraca}, headers=h)
        assert r.status_code == 422


def test_mass_assignment_em_troca_de_senha(login):
    client, h = login()
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": PASSWORD, "new_password": "Nova-Senha-789!", "role": "admin"})
    assert r.status_code == 422


def test_senha_temporaria_bloqueia_ate_trocar(client, make_user):
    make_user(must_change=True)
    r = _login(client)
    h = {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Nova-Senha-789!"}, headers=h).status_code == 204
    assert client.get("/api/auth/me").json()["user"]["must_change_password"] is False


def test_reset_de_senha_fluxo_completo_e_uso_unico(client, make_user):
    from app.modules.auth import mailer
    make_user(email="ana@exemplo.com")
    r = client.post("/api/auth/password-reset/request", json={"email": "ana@exemplo.com"}, headers=ORIGIN)
    assert r.status_code == 202 and len(mailer.OUTBOX) == 1
    token = mailer.OUTBOX[0]["link"].split("token=")[1]
    body = {"token": token, "new_password": "Reset-Senha-321!"}
    assert client.post("/api/auth/password-reset/confirm", json=body, headers=ORIGIN).status_code == 204
    assert client.post("/api/auth/password-reset/confirm", json=body, headers=ORIGIN).status_code == 400  # uso único
    assert _login(client, "ana", "Reset-Senha-321!").status_code == 200
    assert _login(client, "ana", PASSWORD).status_code == 401


def test_reset_nao_enumera_usuarios(client, make_user):
    from app.modules.auth import mailer
    make_user(email="ana@exemplo.com")
    a = client.post("/api/auth/password-reset/request", json={"email": "ana@exemplo.com"}, headers=ORIGIN)
    b = client.post("/api/auth/password-reset/request", json={"email": "ninguem@exemplo.com"}, headers=ORIGIN)
    assert a.status_code == b.status_code == 202 and a.json() == b.json() and len(mailer.OUTBOX) == 1


def test_reset_token_expirado_e_invalido(client, make_user, owner_engine):
    from app.modules.auth import mailer
    make_user(email="ana@exemplo.com")
    client.post("/api/auth/password-reset/request", json={"email": "ana@exemplo.com"}, headers=ORIGIN)
    token = mailer.OUTBOX[0]["link"].split("token=")[1]
    with owner_engine.begin() as c:
        c.execute(text("UPDATE password_reset_tokens SET expires_at = :t"), {"t": timeutil.now(seconds=-1)})
    body = {"token": token, "new_password": "Reset-Senha-321!"}
    assert client.post("/api/auth/password-reset/confirm", json=body, headers=ORIGIN).status_code == 400
    body["token"] = "x" * 43
    assert client.post("/api/auth/password-reset/confirm", json=body, headers=ORIGIN).status_code == 400


def test_reset_com_senha_fraca_nao_consome_token(client, make_user):
    from app.modules.auth import mailer
    make_user(email="ana@exemplo.com")
    client.post("/api/auth/password-reset/request", json={"email": "ana@exemplo.com"}, headers=ORIGIN)
    token = mailer.OUTBOX[0]["link"].split("token=")[1]
    assert client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "fraca"}, headers=ORIGIN).status_code == 422
    assert client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "Reset-Senha-321!"}, headers=ORIGIN).status_code == 204


def test_reset_rate_limit(client):
    codes = [client.post("/api/auth/password-reset/request", json={"email": f"x{i}@e.com"}, headers=ORIGIN).status_code for i in range(8)]
    assert 429 in codes


# ── Erros, headers, auditoria ──
def test_headers_de_seguranca(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "referrer-policy" in r.headers and "permissions-policy" in r.headers


def test_erro_500_nao_vaza_detalhes(client, monkeypatch):
    from app.modules.auth import router as auth_router
    monkeypatch.setattr(auth_router, "verify_password", lambda *a: (_ for _ in ()).throw(RuntimeError("segredo /srv/app senha=abc")))
    r = client.post("/api/auth/login", json={"username": "a", "password": "b"}, headers=ORIGIN)
    assert r.status_code == 500 and "segredo" not in r.text and "/srv" not in r.text and "Traceback" not in r.text


def test_validacao_nao_reflete_a_senha_enviada(client):
    r = client.post("/api/auth/login", json={"username": "a", "password": "S3gredo!" * 100}, headers=ORIGIN)
    assert r.status_code == 422 and "S3gredo" not in r.text


def test_corpo_gigante_413(client):
    r = client.post("/api/auth/login", content=b"x" * 2_000_000, headers={**ORIGIN, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_metodo_inesperado_405(client):
    assert client.put("/api/auth/login", headers=ORIGIN).status_code == 405
    assert client.delete("/api/auth/me", headers=ORIGIN).status_code in (401, 405)


def test_auditoria_registra_e_nunca_grava_segredos(client, make_user, owner_engine):
    make_user()
    _login(client, "ana", "errada-errada-1")
    r = _login(client)
    h = {**ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}
    client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Nova-Senha-789!"}, headers=h)
    client.post("/api/auth/logout", headers=h)
    with owner_engine.begin() as c:
        acts = c.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
        dump = " ".join(str(tuple(r)) for r in c.execute(text("SELECT * FROM audit_log")).all())
    assert {"login_falha", "login", "troca_senha", "logout"} <= set(acts)
    for segredo in [PASSWORD, "Nova-Senha-789!", "errada-errada-1", r.json()["csrf_token"]]:
        assert segredo not in dump


def test_scrub_remove_chaves_sensiveis():
    from app.modules.audit.service import _scrub
    out = _scrub({"password": "x", "novo_token": "y", "ok": 1, "aninhado": {"csrf": "z", "a": 2}})
    assert out == {"password": "[removido]", "novo_token": "[removido]", "ok": 1, "aninhado": {"csrf": "[removido]", "a": 2}}
