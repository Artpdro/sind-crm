import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

export default function ChangePassword() {
  const { user, reload } = useAuth();
  const nav = useNavigate();
  const [cur, setCur] = useState(""); const [nw, setNw] = useState(""); const [nw2, setNw2] = useState("");
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr("");
    if (nw !== nw2) return setErr("A confirmação não confere com a nova senha.");
    setBusy(true);
    try {
      await api("/api/auth/change-password", { method: "POST", body: { current_password: cur, new_password: nw } });
      await reload(); nav("/", { replace: true });
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); }
    finally { setBusy(false); }
  }
  return (
    <section className="card auth-card">
      <h1>{user?.must_change_password ? "Defina sua nova senha" : "Alterar senha"}</h1>
      {user?.must_change_password && <p className="muted">Por segurança, troque a senha temporária antes de continuar.</p>}
      <form onSubmit={submit}>
        <label>Senha atual<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required /></label>
        <label>Nova senha<input type="password" value={nw} onChange={(e) => setNw(e.target.value)} autoComplete="new-password" required minLength={10} /></label>
        <label>Confirmar nova senha<input type="password" value={nw2} onChange={(e) => setNw2(e.target.value)} autoComplete="new-password" required /></label>
        <p className="small muted">Mínimo de 10 caracteres, com letras e ao menos um número ou símbolo.</p>
        {err && <p role="alert" className="error">{err}</p>}
        <button className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : "Salvar nova senha"}</button>
      </form>
    </section>
  );
}
