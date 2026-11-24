import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, baixar } from "../api";
import MapaView from "../components/MapaView";
import { endereco, fmtCnpj, type Bbox, type Campo, type Empresa, type MapaResp, type Opcao } from "../types";

type F = { rua: string; bairro: string; numero: string; cidade: string; paridade: string; porte_id: string; tipo_empresa_id: string; categoria_id: string; situacao_id: string; campos: Record<string, string> };
const VAZIO: F = { rua: "", bairro: "", numero: "", cidade: "", paridade: "todos", porte_id: "", tipo_empresa_id: "", categoria_id: "", situacao_id: "", campos: {} };

function qs(f: F, extra: Record<string, string> = {}) {
  const p = new URLSearchParams(extra);
  for (const k of ["rua", "bairro", "numero", "cidade", "porte_id", "tipo_empresa_id", "categoria_id", "situacao_id"] as const) if (f[k].trim()) p.set(k, f[k].trim());
  if (f.paridade !== "todos") p.set("paridade", f.paridade);
  for (const [k, v] of Object.entries(f.campos)) if (v.trim()) p.append("campo", `${k}:${v.trim()}`);
  return p;
}

export default function Mapa() {
  const nav = useNavigate(); const [sp] = useSearchParams();
  const [f, setF] = useState<F>(VAZIO); const [ap, setAp] = useState<F>(VAZIO);
  const [dados, setDados] = useState<MapaResp | null>(null); const [err, setErr] = useState("");
  const [bbox, setBbox] = useState<Bbox | null>(null); const [ajustar, setAjustar] = useState(0);
  const [lista, setLista] = useState<{ total: number; itens: Empresa[] } | null>(null); const [pagina, setPagina] = useState(1);
  const [sel, setSel] = useState<{ id: number; n: number } | null>(null); const [aviso, setAviso] = useState("");
  const [L, setL] = useState<{ sit: Opcao[]; porte: Opcao[]; tipo: Opcao[]; cat: Opcao[]; campos: Campo[] }>({ sit: [], porte: [], tipo: [], cat: [], campos: [] });
  const [gerando, setGerando] = useState(false);
  const truncado = useRef(false); const foco = useRef(sp.get("empresa"));

  useEffect(() => {
    Promise.all([api<Opcao[]>("/api/cadastros/situacoes"), api<Opcao[]>("/api/cadastros/portes"), api<Opcao[]>("/api/cadastros/tipos-empresa"),
      api<Opcao[]>("/api/cadastros/categorias"), api<Campo[]>("/api/campos")]).then(([sit, porte, tipo, cat, campos]) => setL({ sit, porte, tipo, cat, campos })).catch((e) => setErr(e.message));
  }, []);

  // Marcadores: vêm das coordenadas JÁ GRAVADAS no banco (o mapa nunca geocodifica). Se houver mais que o limite, carrega só a área visível.
  useEffect(() => {
    const extra: Record<string, string> = bbox && truncado.current ? { sul: String(bbox.sul), oeste: String(bbox.oeste), norte: String(bbox.norte), leste: String(bbox.leste) } : {};
    api<MapaResp>(`/api/mapa/empresas?${qs(ap, extra)}`).then((r) => { truncado.current = r.truncado; setDados(r); setErr(""); }).catch((e) => setErr(e.message));
  }, [ap, bbox]);
  useEffect(() => {
    api<{ total: number; itens: Empresa[] }>(`/api/empresas?${qs(ap, { page: String(pagina), page_size: "25" })}`).then(setLista).catch((e) => setErr(e.message));
  }, [ap, pagina]);
  useEffect(() => { if (foco.current && dados) { setSel({ id: +foco.current, n: 1 }); foco.current = null; } }, [dados]);

  const pesquisar = (e?: FormEvent) => { e?.preventDefault(); truncado.current = false; setBbox(null); setPagina(1); setAjustar((n) => n + 1); setAp({ ...f, campos: { ...f.campos } }); };
  const limpar = () => { setF(VAZIO); truncado.current = false; setBbox(null); setPagina(1); setAjustar((n) => n + 1); setAp(VAZIO); };
  const set = (k: keyof F) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const onMover = useCallback((b: Bbox) => { if (truncado.current) setBbox(b); }, []);
  const escolher = (e: Empresa) => { if (e.latitude === null) return setAviso(`"${e.nome}" ainda não tem localização no mapa.`); setAviso(""); setSel({ id: e.id, n: (sel?.n ?? 0) + 1 }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const sel_ = (k: keyof F, rot: string, ops: Opcao[]) => <label>{rot}<select value={f[k] as string} onChange={set(k)}><option value="">Todos</option>{ops.map((o) => <option key={o.id} value={o.id}>{o.nome}</option>)}</select></label>;
  const baixarPdf = async () => {
    setGerando(true); setErr("");
    try { await baixar(`/api/relatorios/empresas.pdf?${qs(ap)}`); } catch (e) { setErr((e as Error).message); } finally { setGerando(false); }
  };
  const paginas = lista ? Math.max(1, Math.ceil(lista.total / 25)) : 1;

  return (
    <section>
      <h1>Mapa de Empresas</h1>
      <form className="card busca" onSubmit={pesquisar}>
        <h2>Pesquisar por:</h2>
        <div className="form-grid">
          <label>Rua<input value={f.rua} onChange={set("rua")} placeholder="Ex.: Rua Marcondes" maxLength={300} /></label>
          <label>Bairro<input value={f.bairro} onChange={set("bairro")} maxLength={150} /></label>
          <label>Número<input value={f.numero} onChange={set("numero")} maxLength={60} /></label>
          <label>Cidade<input value={f.cidade} onChange={set("cidade")} maxLength={150} /></label>
          <label>Filtrar número por:<select value={f.paridade} onChange={set("paridade")}><option value="todos">Todos</option><option value="pares">Apenas números pares</option><option value="impares">Apenas números ímpares</option></select></label>
          {sel_("situacao_id", "Situação", L.sit)}{sel_("porte_id", "Porte", L.porte)}{sel_("tipo_empresa_id", "Tipo de empresa", L.tipo)}{sel_("categoria_id", "Categoria", L.cat)}
          {L.campos.map((c) => (
            <label key={c.chave}>{c.rotulo}
              {c.tipo === "booleano" ? <select value={f.campos[c.chave] ?? ""} onChange={(e) => setF({ ...f, campos: { ...f.campos, [c.chave]: e.target.value } })}><option value="">Todos</option><option value="sim">Sim</option><option value="nao">Não</option></select>
                : c.tipo === "selecao" ? <select value={f.campos[c.chave] ?? ""} onChange={(e) => setF({ ...f, campos: { ...f.campos, [c.chave]: e.target.value } })}><option value="">Todos</option>{c.opcoes?.map((o) => <option key={o}>{o}</option>)}</select>
                : <input type={c.tipo === "data" ? "date" : c.tipo === "numero" ? "number" : "text"} value={f.campos[c.chave] ?? ""} onChange={(e) => setF({ ...f, campos: { ...f.campos, [c.chave]: e.target.value } })} />}
            </label>))}
        </div>
        <div className="row"><div className="row"><button className="btn btn-primary">Pesquisar</button><button type="button" className="btn btn-outline" onClick={limpar}>Limpar</button></div>
          <button type="button" className="btn btn-outline" onClick={baixarPdf} disabled={gerando || !dados || dados.total === 0} title="PDF com exatamente as empresas desta busca: critérios, mapa, dados e última interação">{gerando ? "Gerando PDF…" : "Baixar PDF"}</button></div>
      </form>

      {err && <p role="alert" className="error">{err}</p>}
      <p className="contador" aria-live="polite"><strong>{dados ? `${dados.total} empresas encontradas` : "Carregando…"}</strong>
        {dados && <span className="muted"> · {dados.com_localizacao} no mapa · {dados.sem_localizacao} sem localização
          {dados.excluidos_sem_numero ? ` · ${dados.excluidos_sem_numero} sem número identificável ficaram de fora do filtro par/ímpar` : ""}</span>}</p>
      {dados && dados.sem_localizacao > 0 && dados.com_localizacao === 0 && <p className="aviso">Nenhuma empresa desta busca tem localização ainda. {" "}<Link to="/localizacao">Geocodificar empresas</Link></p>}
      {dados && dados.com_localizacao > 0 && <p className="muted legenda">Pontos menores = localização aproximada (pela rua) · contorno laranja = para revisão · tracejado = endereço alterado, aguardando nova localização.</p>}
      {dados?.truncado && <p className="aviso">Há mais empresas do que o limite exibido de uma vez; o mapa mostra a área visível. Aproxime o zoom ou refine a busca.</p>}

      <div className="mapa-grid">
        <MapaView marcadores={dados?.marcadores ?? []} selecionado={sel} ajustar={ajustar} onVer={(id) => nav(`/empresas/${id}`)} onMover={onMover} />
        <div className="card lista-lateral">
          <h2>Empresas</h2>{aviso && <p className="aviso" role="status">{aviso}</p>}
          <ul className="lista">{lista?.itens.map((e) => (
            <li key={e.id}><button className={"item" + (sel?.id === e.id ? " on" : "")} onClick={() => escolher(e)}>
              <strong>{e.nome}</strong><small>{fmtCnpj(e.cnpj)} · {endereco(e)}</small>
              <small>{[e.cidade, e.situacao_nome, e.porte_nome, e.categorias.map((c) => c.nome).join(", ")].filter(Boolean).join(" · ")}</small>
              {e.latitude === null && <span className="badge" style={{ background: "#6b7280" }}>Sem localização</span>}</button></li>))}</ul>
          <div className="row pager"><button className="btn btn-outline" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>Anterior</button><span>{pagina}/{paginas}</span>
            <button className="btn btn-outline" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}>Próxima</button></div>
        </div>
      </div>
    </section>
  );
}
