import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, baixar } from "../api";
import { TIPOS_INTERACAO, fmtCnpj, type Opcao } from "../types";

type Resumo = { total: number; empresas: number; usuarios: number; por_tipo: Record<string, number>; por_usuario: Record<string, number>; por_dia: Record<string, number> };
type Item = { id: number; data_interacao: string; tipo: string; descricao: string; usuario: string; empresa_id: number; nome: string; cnpj: string | null; cidade: string | null; situacao_resultante: string | null };
type Resp = { resumo: Resumo; page: number; itens: Item[] };

const hoje = (dias = 0) => { const d = new Date(); d.setDate(d.getDate() + dias); return d.toLocaleDateString("en-CA"); };
const dataLocal = (s: string) => new Date(s.replace(" ", "T") + "Z").toLocaleString("pt-BR", { timeZone: "America/Recife", dateStyle: "short", timeStyle: "short" });
const br = (iso: string) => iso.split("-").reverse().join("/");

function Barras({ titulo, dados, rotulo = (k: string) => k }: { titulo: string; dados: Record<string, number>; rotulo?: (k: string) => string }) {
  const max = Math.max(1, ...Object.values(dados));
  return (
    <div className="card"><h2>{titulo}</h2>
      {Object.keys(dados).length === 0 ? <p className="muted">Sem dados.</p> : <ul className="barras">{Object.entries(dados).map(([k, v]) => (
        <li key={k}><span className="barra-rot">{rotulo(k)}</span><span className="barra-trilho"><span className="barra-valor" style={{ width: `${(v / max) * 100}%` }} /></span><strong>{v}</strong></li>))}</ul>}
    </div>
  );
}

export default function Relatorios() {
  const [f, setF] = useState({ inicio: hoje(-6), fim: hoje(), tipo: "", usuario_id: "", situacao_id: "", cidade: "" });
  const [dados, setDados] = useState<Resp | null>(null); const [pagina, setPagina] = useState(1);
  const [err, setErr] = useState(""); const [gerando, setGerando] = useState(false); const [carregando, setCarregando] = useState(false);
  const [usuarios, setUsuarios] = useState<{ id: number; username: string }[]>([]); const [sit, setSit] = useState<Opcao[]>([]);

  useEffect(() => {
    api<{ id: number; username: string }[]>("/api/relatorios/usuarios").then(setUsuarios).catch(() => undefined);
    api<Opcao[]>("/api/cadastros/situacoes").then(setSit).catch(() => undefined);
  }, []);

  const params = (extra: Record<string, string> = {}) => {
    const p = new URLSearchParams({ inicio: f.inicio, fim: f.fim, ...extra });
    for (const k of ["tipo", "usuario_id", "situacao_id", "cidade"] as const) if (f[k].trim()) p.set(k, f[k].trim());
    return p;
  };
  const carregar = async (pg: number) => {
    setErr(""); setCarregando(true);
    try { setDados(await api<Resp>(`/api/relatorios/atividades?${params({ page: String(pg), page_size: "25" })}`)); setPagina(pg); }
    catch (e) { setErr((e as Error).message); setDados(null); } finally { setCarregando(false); }
  };
  const enviar = (e: FormEvent) => { e.preventDefault(); void carregar(1); };
  const pdf = async () => { setGerando(true); setErr(""); try { await baixar(`/api/relatorios/atividades.pdf?${params()}`); } catch (e) { setErr((e as Error).message); } finally { setGerando(false); } };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const paginas = dados ? Math.max(1, Math.ceil(dados.resumo.total / 25)) : 1;

  return (
    <section>
      <h1>Relatórios</h1>
      <form className="card busca" onSubmit={enviar}>
        <h2>Atividades por data</h2>
        <p className="muted">Baseado nas interações registradas nas empresas (ligações, visitas, e-mails…). As datas seguem o horário de Recife.</p>
        <div className="form-grid">
          <label>De<input type="date" required value={f.inicio} max={f.fim} onChange={set("inicio")} /></label>
          <label>Até<input type="date" required value={f.fim} min={f.inicio} onChange={set("fim")} /></label>
          <label>Tipo de atividade<select value={f.tipo} onChange={set("tipo")}><option value="">Todos</option>{Object.entries(TIPOS_INTERACAO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>Responsável<select value={f.usuario_id} onChange={set("usuario_id")}><option value="">Todos</option>{usuarios.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
          <label>Situação atual da empresa<select value={f.situacao_id} onChange={set("situacao_id")}><option value="">Todas</option>{sit.map((o) => <option key={o.id} value={o.id}>{o.nome}</option>)}</select></label>
          <label>Cidade<input value={f.cidade} onChange={set("cidade")} maxLength={150} /></label>
        </div>
        <div className="row">
          <div className="row">
            <button className="btn btn-primary" disabled={carregando}>{carregando ? "Buscando…" : "Ver atividades"}</button>
            <button type="button" className="btn btn-outline" onClick={() => { setF({ ...f, inicio: hoje(-6), fim: hoje() }); }}>Últimos 7 dias</button>
            <button type="button" className="btn btn-outline" onClick={() => { const d = new Date(); setF({ ...f, inicio: new Date(d.getFullYear(), d.getMonth(), 1).toLocaleDateString("en-CA"), fim: hoje() }); }}>Este mês</button>
          </div>
          <button type="button" className="btn btn-outline" onClick={pdf} disabled={gerando}>{gerando ? "Gerando PDF…" : "Baixar PDF"}</button>
        </div>
      </form>
      {err && <p role="alert" className="error">{err}</p>}
      {dados && (
        <>
          <p className="contador" aria-live="polite"><strong>{dados.resumo.total} atividade(s)</strong>
            <span className="muted"> · {dados.resumo.empresas} empresa(s) atendida(s) · {dados.resumo.usuarios} responsável(is) · {br(f.inicio)} a {br(f.fim)}</span></p>
          <div className="grid2">
            <Barras titulo="Por tipo" dados={dados.resumo.por_tipo} rotulo={(k) => TIPOS_INTERACAO[k] ?? k} />
            <Barras titulo="Por responsável" dados={dados.resumo.por_usuario} />
            <Barras titulo="Por dia" dados={dados.resumo.por_dia} rotulo={(k) => br(k)} />
          </div>
          <div className="card"><h2>Atividades (mais recentes primeiro)</h2>
            {dados.itens.length === 0 ? <p className="muted">Nenhuma atividade neste período.</p> :
              <ul className="lista">{dados.itens.map((i) => (
                <li key={i.id} className="item-ativ"><strong>{dataLocal(i.data_interacao)}</strong> · {TIPOS_INTERACAO[i.tipo] ?? i.tipo} · {i.usuario}<br />
                  <Link to={`/empresas/${i.empresa_id}`}>{i.nome}</Link> <small className="muted">{[i.cnpj ? fmtCnpj(i.cnpj) : "", i.cidade].filter(Boolean).join(" · ")}</small><br />
                  <span>{i.descricao}</span>{i.situacao_resultante && <small className="muted"> → situação: {i.situacao_resultante}</small>}</li>))}</ul>}
            <div className="row pager"><button className="btn btn-outline" disabled={pagina <= 1} onClick={() => void carregar(pagina - 1)}>Anterior</button><span>{pagina}/{paginas}</span>
              <button className="btn btn-outline" disabled={pagina >= paginas} onClick={() => void carregar(pagina + 1)}>Próxima</button></div>
          </div>
        </>)}
      <p className="muted">Para um PDF das empresas de uma rua ou número específicos, use o <Link to="/mapa">Mapa</Link>, filtre e clique em <strong>Baixar PDF</strong>.</p>
    </section>
  );
}
