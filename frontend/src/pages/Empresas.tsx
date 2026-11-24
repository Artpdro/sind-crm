import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { endereco, fmtCnpj, type Empresa, type Opcao } from "../types";

type Resp = { total: number; page: number; page_size: number; itens: Empresa[] };

export default function Empresas() {
  const { user } = useAuth();
  const [sp] = useSearchParams(); const [revisao, setRevisao] = useState(sp.get("revisao") === "true");
  const [q, setQ] = useState(""); const [cidade, setCidade] = useState(""); const [sit, setSit] = useState("");
  const [page, setPage] = useState(1); const [data, setData] = useState<Resp | null>(null); const [err, setErr] = useState("");
  const [sits, setSits] = useState<Opcao[]>([]);
  useEffect(() => { api<Opcao[]>("/api/cadastros/situacoes").then(setSits).catch(() => {}); }, []);

  const buscar = useCallback(async (p: number) => {
    const qs = new URLSearchParams({ page: String(p), page_size: "25" });
    if (q.trim()) qs.set("q", q.trim()); if (cidade.trim()) qs.set("cidade", cidade.trim()); if (sit) qs.set("situacao_id", sit); if (revisao) qs.set("revisao", "true");
    try { setData(await api<Resp>(`/api/empresas?${qs}`)); setErr(""); } catch (e) { setErr((e as Error).message); }
  }, [q, cidade, sit, revisao]);
  useEffect(() => { void buscar(page); }, [page]); // eslint-disable-line react-hooks/exhaustive-deps
  const aplicar = () => { setPage(1); void buscar(1); };
  const paginas = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section>
      <div className="row"><h1>Empresas</h1>{user?.role !== "visualizador" && <Link className="btn btn-primary" to="/empresas/nova">Cadastrar empresa</Link>}</div>
      <form className="card filters" onSubmit={(e) => { e.preventDefault(); aplicar(); }}>
        <label>Buscar<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nome, fantasia ou CNPJ" maxLength={100} /></label>
        <label>Cidade<input value={cidade} onChange={(e) => setCidade(e.target.value)} maxLength={150} /></label>
        <label>Situação<select value={sit} onChange={(e) => setSit(e.target.value)}><option value="">Todas</option>{sits.map((s) => <option key={s.id} value={s.id}>{s.nome}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={revisao} onChange={(e) => setRevisao(e.target.checked)} />Somente para revisão</label>
        <button className="btn btn-primary">Filtrar</button>
      </form>
      {err && <p role="alert" className="error">{err}</p>}
      <p className="muted" aria-live="polite">{data ? `${data.total} empresas encontradas` : "Carregando…"}</p>
      <div className="table-wrap card">
        <table>
          <thead><tr><th>Empresa</th><th>CNPJ</th><th>Endereço</th><th>Situação</th></tr></thead>
          <tbody>{data?.itens.map((e) => (
            <tr key={e.id}>
              <td><Link to={`/empresas/${e.id}`}>{e.nome}</Link>{e.precisa_revisao && <span className="badge" style={{ background: "#b45309", marginLeft: 8 }}>Revisar</span>}{e.nome_fantasia && <><br /><small className="muted">{e.nome_fantasia}</small></>}</td>
              <td>{fmtCnpj(e.cnpj)}</td><td>{endereco(e)}</td>
              <td>{e.situacao_nome ? <span className="badge" style={{ background: e.situacao_cor ?? "#6b7280" }}>{e.situacao_nome}</span> : "—"}</td>
            </tr>))}</tbody>
        </table>
      </div>
      <div className="row pager">
        <button className="btn btn-outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</button>
        <span>Página {page} de {paginas}</span>
        <button className="btn btn-outline" disabled={page >= paginas} onClick={() => setPage(page + 1)}>Próxima</button>
      </div>
    </section>
  );
}
