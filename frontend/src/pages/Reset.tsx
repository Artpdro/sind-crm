import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";

export function ForgotPassword() {
  const [email, setEmail] = useState(""); const [done, setDone] = useState(false); const [err, setErr] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault(); setErr("");
    try { await api("/api/auth/password-reset/request", { method: "POST", body: { email: email.trim() } }); setDone(true); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); }
  }
  return (
    <section className="card auth-card">
      <h1>Recuperar senha</h1>
      {done ? <p>Se o e-mail estiver cadastrado, enviaremos as instruções. Verifique sua caixa de entrada.</p> : (
        <form onSubmit={submit}>
          <label>E-mail cadastrado<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required /></label>
          {err && <p role="alert" className="error">{err}</p>}
          <button className="btn btn-primary">Enviar instruções</button>
        </form>
      )}
      <p className="small"><Link to="/login">Voltar ao login</Link></p>
    </section>
  );
}

export function ResetPassword() {
  const nav = useNavigate();
  // O token vem no fragmento (#token=...): o navegador não o envia ao servidor nem em Referer.
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
  const [pw, setPw] = useState(""); const [err, setErr] = useState(""); const [ok, setOk] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setErr("");
    try { await api("/api/auth/password-reset/confirm", { method: "POST", body: { token, new_password: pw } }); setOk(true); history.replaceState(null, "", "/redefinir-senha"); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); }
  }
  if (ok) return <section className="card auth-card"><h1>Senha redefinida</h1><p>Agora você já pode entrar.</p><button className="btn btn-primary" onClick={() => nav("/login")}>Ir para o login</button></section>;
  return (
    <section className="card auth-card">
      <h1>Nova senha</h1>
      {!token ? <p className="error">Link inválido. Solicite uma nova recuperação.</p> : (
        <form onSubmit={submit}>
          <label>Nova senha<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" required minLength={10} /></label>
          {err && <p role="alert" className="error">{err}</p>}
          <button className="btn btn-primary">Redefinir senha</button>
        </form>
      )}
    </section>
  );
}
