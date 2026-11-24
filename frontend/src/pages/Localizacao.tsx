import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { GEO_STATUS, PRECISAO, endereco, type Diagnostico, type Empresa, type ResumoGeo } from "../types";

type Lote = ResumoGeo & { interrompido?: boolean; localizado: number; revisao: number; nao_encontrado: number; pulada: number };

export default function Localizacao() {
  const { user } = useAuth();
  const [d, setD] = useState<Diagnostico | null>(null); const [err, setErr] = useState("");
  const [rodando, setRodando] = useState(false); const [msg, setMsg] = useState(""); const [retentar, setRetentar] = useState(false);
  const [teste, setTeste] = useState<{ ok: boolean; mensagem: string; ms: number } | null>(null); const [escopo, setEscopo] = useState("nao_encontradas"); const [info, setInfo] = useState("");
  const parar = useRef(false);
  const [aba, setAba] = useState<"revisao" | "nao_encontrado">("revisao"); const [pagina, setPagina] = useState(1);
  const [lista, setLista] = useState<{ total: number; itens: Empresa[] } | null>(null);
  const r = d?.resumo ?? null; const admin = user?.role === "admin";

  const carregar = useCallback(async () => { try { setD(await api<Diagnostico>("/api/geocodificacao/diagnostico")); } catch (e) { setErr((e as Error).message); } }, []);
  useEffect(() => { void carregar(); }, [carregar]);
  useEffect(() => { api<{ total: number; itens: Empresa[] }>(`/api/empresas?geocode_status=${aba}&page=${pagina}&page_size=25`).then(setLista).catch((e) => setErr(e.message)); }, [aba, pagina, rodando, info]);
  useEffect(() => {  // acompanha o andamento enquanto houver empresas na fila
    if (!r || r.pendente === 0) return;
    const t = setInterval(() => { void carregar(); }, 8000);
    return () => clearInterval(t);
  }, [r?.pendente, carregar]); // eslint-disable-line react-hooks/exhaustive-deps

  async function testar() {
    setErr(""); setTeste(null);
    try { setTeste(await api("/api/geocodificacao/testar", { method: "POST" })); void carregar(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); }
  }
  async function refazer() {
    const nome = { nao_encontradas: "as NÃO ENCONTRADAS", revisao: "as PARA REVISÃO", todas: "TODAS (exceto as corrigidas manualmente)" }[escopo];
    if (!confirm(`Devolver ${nome} à fila para tentar de novo com a busca atual? Os pontos já gravados ficam no mapa até haver outros.`)) return;
    try { const x = await api<{ empresas_na_fila: number }>("/api/geocodificacao/reprocessar", { method: "POST", body: { escopo } }); setInfo(`${x.empresas_na_fila} empresas voltaram para a fila.`); void carregar(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); }
  }
  async function geocodificar() {
    if (!confirm("Os endereços das empresas serão enviados ao serviço Nominatim (OpenStreetMap), a 1 por segundo. Mantenha esta página aberta até terminar. Continuar?")) return;
    setErr(""); setRodando(true); parar.current = false; let feitas = 0;
    try {
      for (;;) {
        const l = await api<Lote>("/api/geocodificacao/lote", { method: "POST", body: { limite: 10, retentar_falhas: retentar } });
        const n = l.localizado + l.revisao + l.nao_encontrado + l.pulada; feitas += n; setMsg(`${feitas} processadas…`);
        if (l.interrompido) { setErr("O serviço de geocodificação parou de responder. Veja o último erro abaixo (nada se perdeu)."); break; }
        if (parar.current || n === 0 || (l.pendente === 0 && !retentar)) break;
      }
      setMsg(parar.current ? "Pausado. Você pode continuar depois." : "Concluído.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); } finally { setRodando(false); void carregar(); }
  }

  const sv = r?.servico; const erroAtual = !!(sv?.ultimo_erro && (!sv.ultimo_ok_em || (sv.ultimo_erro_em ?? "") > sv.ultimo_ok_em));
  const aprox = (d?.por_precisao.interpolado ?? 0) + (d?.por_precisao.rua ?? 0) + (d?.por_precisao.cep ?? 0) + (d?.por_precisao.bairro ?? 0);
  const cards: [string, number | undefined][] = [["Localizadas", r?.localizado], ["Pendentes (na fila)", r?.pendente], ["Para revisão", r?.revisao], ["Não encontradas", r?.nao_encontrado], ["Endereço alterado", r?.desatualizadas]];
  const paginas = lista ? Math.max(1, Math.ceil(lista.total / 25)) : 1;
  return (
    <section>
      <h1>Localização das empresas</h1>
      <p className="muted">O mapa usa as coordenadas já gravadas no banco e <strong>nunca</strong> consulta serviços externos ao abrir. A geocodificação acontece só aqui, ou quando o endereço muda.</p>
      {err && <p role="alert" className="error">{err}</p>}
      <div className="grid">{cards.map(([t, n]) => <div key={t} className="card stat"><strong>{n ?? "—"}</strong><span>{t}</span></div>)}</div>
      {r && (r.automatica
        ? <p className="aviso ok" role="status"><strong>Geocodificação automática ativa.</strong> Toda empresa nova (cadastro ou importação) ou com endereço alterado é mapeada sozinha, em segundo plano.
            {r.pendente > 0 && <> Na fila agora: <strong>{r.pendente}</strong> (≈ {r.estimativa_min} min).</>}</p>
        : <p className="aviso" role="status"><strong>A geocodificação automática está DESLIGADA</strong> (<code>GEOCODER_AUTO=false</code> no <code>.env</code>), por isso as empresas ficam <em>pendentes</em>. Ligue e reinicie a API, ou use o botão abaixo / <code>python -m scripts.geocode</code>.</p>)}
      {r && r.pendente > 0 && r.automatica && !sv?.consultas && !sv?.ultimo_erro && <p className="aviso">Ainda sem nenhuma resposta do serviço neste momento. Se isso não mudar em alguns segundos, use <strong>Testar conexão</strong> abaixo.</p>}
      {erroAtual && <p className="aviso erro-servico" role="alert"><strong>Último erro do serviço de mapas:</strong> {sv?.ultimo_erro}<br />
        <small>Erro 403/429 = o OpenStreetMap está bloqueando ou limitando este computador (confira <code>GEOCODER_EMAIL</code> e espere). "Não foi possível falar com o serviço" = verifique a internet/firewall.</small></p>}
      {r && !r.contato_configurado && <p className="aviso">Defina <code>GEOCODER_EMAIL</code> no arquivo <code>.env</code> do backend: a política de uso do Nominatim exige um contato.</p>}

      <div className="card"><h2>Diagnóstico</h2>
        <p className="muted">{r?.localizado ?? 0} localizadas{aprox ? `, das quais ${aprox} são aproximadas (pela rua, CEP ou bairro)` : ""}. <strong>Localizada</strong> = achada na cidade certa, com o número ou pela rua (o OpenStreetMap quase nunca tem o número no Brasil).
          {d && d.sem_rua_ou_cidade > 0 && <> {d.sem_rua_ou_cidade} empresas não têm rua ou cidade no cadastro e não podem ser mapeadas.</>}</p>
        {d && Object.keys(d.por_precisao).length > 0 && <p>{Object.entries(d.por_precisao).map(([k, v]) => <span key={k} className="badge" style={{ background: "#215d98", marginRight: 6 }}>{PRECISAO[k] ?? k}: {v}</span>)}</p>}
        {d && d.motivos.length > 0 && <div className="table-wrap"><table><thead><tr><th>Quantidade</th><th>Situação</th><th>Motivo</th></tr></thead><tbody>
          {d.motivos.map((m, i) => <tr key={i}><td>{m.quantidade}</td><td>{GEO_STATUS[m.status]}</td><td>{m.motivo}</td></tr>)}</tbody></table></div>}
        {admin && <div className="row" style={{ marginTop: 12 }}><div className="row"><button className="btn btn-outline" onClick={testar}>Testar conexão com o serviço</button>
          {teste && <span role="status" className={teste.ok ? "" : "error"}>{teste.ok ? "✓ " : "✗ "}{teste.mensagem} ({teste.ms} ms)</span>}</div></div>}
      </div>

      {admin && (
        <div className="card"><h2>Geocodificar / refazer</h2>
          <p className="muted">Normalmente não é preciso: a automática cuida da fila. Cada empresa leva cerca de 1 segundo. Para muitas empresas (≈ 1h30 para 3.900), prefira o terminal: <code>python -m scripts.geocode</code> (pode pausar e continuar; <code>--amostra 15 --dry-run --detalhe</code> testa sem gravar).</p>
          <div className="row"><div className="row"><label>Refazer<select value={escopo} onChange={(e) => setEscopo(e.target.value)}>
            <option value="nao_encontradas">as não encontradas</option><option value="revisao">as para revisão</option><option value="todas">todas (menos as manuais)</option></select></label>
            <button className="btn btn-primary" onClick={refazer}>Devolver à fila</button></div><span className="muted" role="status">{info}</span></div>
          <hr />
          <label className="check"><input type="checkbox" checked={retentar} onChange={(e) => setRetentar(e.target.checked)} disabled={rodando} />Incluir as "não encontradas"</label>
          <div className="row"><div className="row"><button className="btn btn-outline" onClick={geocodificar} disabled={rodando || (!retentar && (r?.pendente ?? 0) === 0)}>{rodando ? "Geocodificando…" : "Geocodificar pendentes agora"}</button>
            {rodando && <button className="btn btn-outline" onClick={() => { parar.current = true; }}>Pausar</button>}</div><span className="muted" aria-live="polite">{msg}</span></div></div>)}

      <div className="card"><h2>Conferir</h2>
        <div className="tabs">{([["revisao", "Para revisão"], ["nao_encontrado", "Não encontradas"]] as const).map(([k, t]) => <button key={k} className={aba === k ? "tab on" : "tab"} onClick={() => { setAba(k); setPagina(1); }}>{t}</button>)}</div>
        <p className="muted">{aba === "revisao" ? "Achada só pelo CEP/bairro, em rodovia/estrada, ou numa cidade diferente da cadastrada. Confira no perfil e corrija se precisar." : "O serviço não achou o endereço. Corrija o endereço ou informe a localização manualmente no perfil."}</p>
        <div className="table-wrap"><table><thead><tr><th>Empresa</th><th>Endereço</th><th>Situação da localização</th></tr></thead><tbody>
          {lista?.itens.map((e) => (<tr key={e.id}><td><Link to={`/empresas/${e.id}`}>{e.nome}</Link></td><td>{endereco(e)}</td><td>{GEO_STATUS[e.geocode_status]}{e.geocode_erro && <><br /><small className="muted">{e.geocode_erro}</small></>}</td></tr>))}
          {lista && lista.itens.length === 0 && <tr><td colSpan={3} className="muted">Nenhuma empresa nesta situação.</td></tr>}</tbody></table></div>
        <div className="row pager"><button className="btn btn-outline" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>Anterior</button><span>Página {pagina} de {paginas}</span>
          <button className="btn btn-outline" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}>Próxima</button></div></div>
    </section>
  );
}
