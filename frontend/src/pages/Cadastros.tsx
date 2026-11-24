import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { Opcao } from "../types";

const ABAS: [string, string][] = [["situacoes", "Situações"], ["categorias", "Categorias"], ["portes", "Portes"], ["tipos-empresa", "Tipos de empresa"]];

export default function Cadastros() {
  const [aba, setAba] = useState("situacoes"); const [itens, setItens] = useState<Opcao[]>([]); const [nome, setNome] = useState(""); const [err, setErr] = useState("");
  const carregar = useCallback(async () => { try { setItens(await api<Opcao[]>(`/api/cadastros/${aba}?todas=true`)); setErr(""); } catch (e) { setErr((e as Error).message); } }, [aba]);
  useEffect(() => { void carregar(); }, [carregar]);
  const agir = async (fn: () => Promise<unknown>) => { try { await fn(); setErr(""); await carregar(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); } };
  const criar = (ev: FormEvent) => { ev.preventDefault(); void agir(async () => { await api(`/api/cadastros/${aba}`, { method: "POST", body: { nome } }); setNome(""); }); };
  const renomear = (i: Opcao) => { const n = prompt("Novo nome:", i.nome); if (n && n.trim() && n !== i.nome) void agir(() => api(`/api/cadastros/${aba}/${i.id}`, { method: "PATCH", body: { nome: n } })); };

  return (
    <section>
      <h1>Cadastros</h1><p className="muted">Situações, categorias, portes e tipos não ficam fixos no código: você os administra aqui.</p>
      <div className="tabs" role="tablist">{ABAS.map(([k, r]) => <button key={k} role="tab" aria-selected={aba === k} className={aba === k ? "tab on" : "tab"} onClick={() => setAba(k)}>{r}</button>)}</div>
      <form className="card filters" onSubmit={criar}><label>Novo item<input value={nome} onChange={(e) => setNome(e.target.value)} maxLength={80} required /></label><button className="btn btn-primary">Adicionar</button></form>
      {err && <p role="alert" className="error">{err}</p>}
      <div className="table-wrap card"><table><thead><tr><th>Nome</th><th>Ativo</th><th>Ações</th></tr></thead><tbody>
        {itens.map((i) => (<tr key={i.id}><td>{i.nome}</td><td>{i.ativa ? "Sim" : "Não"}</td><td className="acoes">
          <button className="btn btn-outline" onClick={() => renomear(i)}>Renomear</button>
          <button className="btn btn-outline" onClick={() => agir(() => api(`/api/cadastros/${aba}/${i.id}`, { method: "PATCH", body: { ativa: !i.ativa } }))}>{i.ativa ? "Desativar" : "Ativar"}</button>
          <button className="btn btn-danger" onClick={() => confirm(`Excluir "${i.nome}"?`) && agir(() => api(`/api/cadastros/${aba}/${i.id}`, { method: "DELETE" }))}>Excluir</button></td></tr>))}
      </tbody></table></div>
    </section>
  );
}
