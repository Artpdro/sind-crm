import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { endereco, fmtCnpj, type Empresa } from "../types";

type Receita = { situacao: "ativa" | "inativa" | "inapta"; descricao: string; consultado_em: string; do_cache: boolean };
const ROTULO = { ativa: "Ativa", inativa: "Inativa", inapta: "Inapta" } as const;

export default function Consultar() {
  const { user } = useAuth();
  const [cnpj, setCnpj] = useState(""); const [emp, setEmp] = useState<Empresa | null>(null);
  const [naoAchou, setNaoAchou] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const [rf, setRf] = useState<Receita | null>(null); const [rfErr, setRfErr] = useState(""); const [rfBusy, setRfBusy] = useState(false); const [rfCnpj, setRfCnpj] = useState("");

  async function consultarReceita(digitos: string) {
    setRf(null); setRfErr(""); setRfCnpj(digitos); setRfBusy(true);
    try { setRf(await api<Receita>(`/api/receita/situacao/${digitos}`)); }
    catch (x) { if (!(x instanceof ApiError && x.status === 422)) setRfErr(x instanceof ApiError ? x.message : "Falha de conexão."); }  // 422: o CNPJ inválido já é avisado acima
    finally { setRfBusy(false); }
  }

  async function buscar(e: FormEvent) {
    e.preventDefault(); setEmp(null); setNaoAchou(""); setErr(""); setRf(null); setRfErr(""); setRfCnpj("");
    const digitos = cnpj.replace(/\D/g, "");  // o servidor também normaliza; aqui evitamos "/" na URL
    if (!digitos) return setErr("Informe o CNPJ.");
    setBusy(true);
    void consultarReceita(digitos);  // em paralelo com a busca no cadastro
    try { setEmp((await api<{ empresa: Empresa }>(`/api/empresas/cnpj/${digitos}`)).empresa); }
    catch (x) {
      if (x instanceof ApiError && x.status === 404) setNaoAchou(digitos); else setErr(x instanceof ApiError ? x.message : "Falha de conexão.");
    } finally { setBusy(false); }
  }
  return (
    <section>
      <h1>Consultar empresa</h1>
      <form className="card filters" onSubmit={buscar}>
        <label>CNPJ<input value={cnpj} onChange={(e) => setCnpj(e.target.value)} placeholder="00.000.000/0000-00" inputMode="numeric" maxLength={30} /></label>
        <button className="btn btn-primary" disabled={busy}>{busy ? "Consultando…" : "Consultar"}</button>
      </form>
      {err && <p role="alert" className="error">{err}</p>}
      {naoAchou && (
        <div className="card"><h2>Empresa não encontrada.</h2><p className="muted">Nenhuma empresa cadastrada com o CNPJ {fmtCnpj(naoAchou)}.</p>
          {user?.role !== "visualizador" && <Link className="btn btn-primary" to={`/empresas/nova?cnpj=${naoAchou}`}>Cadastrar nova empresa</Link>}</div>)}
      {(rfBusy || rf || rfErr) && (
        <div className="card" aria-live="polite"><h2>Situação cadastral na Receita Federal</h2>
          {rfBusy && <p className="muted">Consultando a Receita Federal…</p>}
          {rf && <p><span className={`sit-rf sit-${rf.situacao}`}>{ROTULO[rf.situacao]}</span>{" "}
            <span className="muted">({rf.descricao}) · CNPJ {fmtCnpj(rfCnpj)}</span></p>}
          {rfErr && <><p role="alert" className="error">{rfErr}</p><button className="btn" type="button" onClick={() => void consultarReceita(rfCnpj)}>Tentar novamente</button></>}
        </div>)}
      {emp && (
        <div className="card"><h2>{emp.nome}</h2>
          <p>{emp.nome_fantasia ?? ""}<br /><strong>CNPJ:</strong> {fmtCnpj(emp.cnpj)}<br /><strong>Endereço:</strong> {endereco(emp)}<br />
            <strong>Situação:</strong> {emp.situacao_nome ?? "—"} · <strong>Porte:</strong> {emp.porte_nome ?? "—"} · <strong>Tipo:</strong> {emp.tipo_nome ?? "—"}</p>
          <Link className="btn btn-primary" to={`/empresas/${emp.id}`}>Ver perfil completo</Link></div>)}
    </section>
  );
}
