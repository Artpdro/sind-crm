import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api";
import logo from "../assets/logo.png";
import { useAuth } from "../auth";

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={user.must_change_password ? "/trocar-senha" : "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    try { await login(username.trim(), password); nav("/", { replace: true }); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão. Tente novamente."); setP(""); }
    finally { setBusy(false); }
  }
  return (
    <section className="card auth-card">
      <img className="auth-logo" src={logo} alt="Sindnorte PE" />
      <h1>Entrar</h1>
      <p className="muted">Acesso restrito a usuários autorizados do Sindnorte.</p>
      <form onSubmit={submit} noValidate>
        <label>Usuário
          <input value={username} onChange={(e) => setU(e.target.value)} autoComplete="username" autoCapitalize="none"
                 spellCheck={false} required maxLength={150} />
        </label>
        <label>Senha
          <input type="password" value={password} onChange={(e) => setP(e.target.value)} autoComplete="current-password"
                 required maxLength={256} />
        </label>
        {err && <p role="alert" className="error">{err}</p>}
        <button className="btn btn-primary" disabled={busy || !username || !password}>{busy ? "Entrando…" : "Entrar"}</button>
      </form>
      <p className="small"><Link to="/esqueci-senha">Esqueci minha senha</Link></p>
    </section>
  );
}
