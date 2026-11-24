import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { GEO_STATUS, PRECISAO, TIPOS_INTERACAO, fmtCnpj, fmtData, type Empresa, type Interacao, type Opcao } from "../types";

const V = ({ v }: { v: string | number | null | undefined }) => <>{v === null || v === undefined || v === "" ? "—" : v}</>;

export default function EmpresaPerfil() {
  const { id } = useParams(); const nav = useNavigate(); const { user } = useAuth();
  const [e, setE] = useState<Empresa | null>(null); const [hist, setHist] = useState<Interacao[]>([]); const [err, setErr] = useState("");
  const [sits, setSits] = useState<Opcao[]>([]);
  const [tipo, setTipo] = useState("visita"); const [desc, setDesc] = useState(""); const [obs, setObs] = useState(""); const [sit, setSit] = useState("");
  const escreve = user?.role !== "visualizador";

  const carregar = useCallback(async () => {
    try {
      setE(await api<Empresa>(`/api/empresas/${id}`));
      setHist((await api<{ itens: Interacao[] }>(`/api/empresas/${id}/interacoes?page_size=100`)).itens);
    } catch (x) { setErr((x as Error).message); }
  }, [id]);
  useEffect(() => { void carregar(); api<Opcao[]>("/api/cadastros/situacoes").then(setSits).catch(() => {}); }, [carregar]);

  useEffect(() => {  // localização na fila: atualiza sozinho até ficar pronta (por até ~3 min)
    if (e?.geocode_status !== "pendente") return;
    let n = 0;
    const t = setInterval(() => { if (++n > 45) clearInterval(t); else void carregar(); }, 4000);
    return () => clearInterval(t);
  }, [e?.geocode_status, carregar]);

  async function registrar(ev: FormEvent) {
    ev.preventDefault(); setErr("");
    try {
      await api(`/api/empresas/${id}/interacoes`, { method: "POST", body: { tipo, descricao: desc, observacoes: obs.trim() || null, situacao_resultante_id: sit ? +sit : null } });
      setDesc(""); setObs(""); setSit(""); await carregar();
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); }
  }
  async function excluir() {
    if (!confirm("Excluir esta empresa? O histórico de interações será preservado no banco.")) return;
    try { await api(`/api/empresas/${id}`, { method: "DELETE" }); nav("/empresas"); } catch (x) { setErr((x as Error).message); }
  }
  const [la, setLa] = useState(""); const [lo, setLo] = useState(""); const [msgLoc, setMsgLoc] = useState("");
  async function corrigirLocal(ev: FormEvent) {
    ev.preventDefault(); setMsgLoc(""); setErr("");
    const lat = Number(la.replace(",", ".")), lon = Number(lo.replace(",", "."));
    if (!la.trim() || !lo.trim() || !isFinite(lat) || !isFinite(lon)) return setErr("Informe latitude e longitude como números (ex.: -7.9407 e -34.8728).");
    try { await api(`/api/empresas/${id}/localizacao`, { method: "PUT", body: { latitude: lat, longitude: lon } }); setMsgLoc("Localização salva."); setLa(""); setLo(""); await carregar(); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); }
  }
  async function novaGeo() {
    try { await api(`/api/empresas/${id}/geocodificar`, { method: "POST" }); setMsgLoc("Marcada para nova geocodificação (acontece no próximo lote em Localização). A localização atual foi mantida."); await carregar(); }
    catch (x) { setErr((x as Error).message); }
  }
  async function revisada() {
    try { setE(await api<Empresa>(`/api/empresas/${id}/revisado`, { method: "POST" })); } catch (x) { setErr((x as Error).message); }
  }
  if (err && !e) return <p role="alert" className="error">{err}</p>;
  if (!e) return <p className="muted">Carregando…</p>;

  return (
    <section>
      <div className="row"><h1>{e.nome}</h1>
        <div className="row">{escreve && <Link className="btn btn-primary" to={`/empresas/${e.id}/editar`}>Editar</Link>}
          {user?.role === "admin" && <button className="btn btn-danger" onClick={excluir}>Excluir</button>}</div></div>
      {e.precisa_revisao && (
        <div className="card aviso-box" role="status"><h2>Precisa de revisão</h2>
          <ul>{e.motivos_revisao.map((m, i) => <li key={i}>{m}</li>)}</ul>
          {e.cnpj_original && <p><strong>CNPJ como veio na planilha:</strong> {e.cnpj_original}</p>}
          {escreve && <button className="btn btn-primary" onClick={revisada}>Marcar como revisada</button>}</div>)}
      <div className="grid2">
        <article className="card"><h2>Dados da empresa</h2><dl>
          <dt>Nome Fantasia</dt><dd><V v={e.nome_fantasia} /></dd><dt>CNPJ</dt><dd>{fmtCnpj(e.cnpj)}</dd>
          <dt>Porte</dt><dd><V v={e.porte_nome} /></dd><dt>Tipo</dt><dd><V v={e.tipo_nome} /></dd>
          <dt>Categoria</dt><dd>{e.categorias.length ? e.categorias.map((c) => c.nome).join(", ") : "—"}</dd>
          <dt>Situação</dt><dd>{e.situacao_nome ? <span className="badge" style={{ background: e.situacao_cor ?? "#6b7280" }}>{e.situacao_nome}</span> : "—"}</dd></dl></article>
        <article className="card"><h2>Contatos</h2><dl>
          <dt>Email</dt><dd><V v={e.email} /></dd><dt>Emails adicionais</dt><dd>{e.emails_adicionais.length ? e.emails_adicionais.join(", ") : "—"}</dd>
          <dt>Celular</dt><dd><V v={e.celular} /></dd><dt>Fone</dt><dd><V v={e.fone} /></dd></dl></article>
        <article className="card"><h2>Endereço</h2><dl>
          <dt>Rua</dt><dd><V v={e.rua} /></dd><dt>Número</dt><dd><V v={e.numero} /></dd><dt>Complemento</dt><dd><V v={e.complemento} /></dd>
          <dt>Bairro</dt><dd><V v={e.bairro} /></dd><dt>Cidade / UF</dt><dd><V v={[e.cidade, e.estado].filter(Boolean).join(" - ")} /></dd><dt>CEP</dt><dd><V v={e.cep} /></dd>
          <dt>Latitude / Longitude</dt><dd>{e.latitude !== null ? `${e.latitude}, ${e.longitude}` : "—"}</dd>
          <dt>Localização</dt><dd>{GEO_STATUS[e.geocode_status]}{e.geocode_desatualizada ? " (endereço alterado; aguardando nova geocodificação)" : ""}</dd></dl></article>
        <article className="card"><h2>Localização no mapa</h2>
          <p><strong>{GEO_STATUS[e.geocode_status]}</strong>{e.geocode_precisao && <span className="muted"> · {PRECISAO[e.geocode_precisao] ?? e.geocode_precisao}</span>}</p>
          {e.geocode_status === "pendente" && !e.geocode_desatualizada && <p className="muted" role="status">Na fila para ser localizada no mapa automaticamente…</p>}
          {e.geocode_desatualizada && <p className="aviso">O endereço mudou: a localização abaixo é a anterior e será atualizada na próxima geocodificação.</p>}
          {e.geocode_erro && <p className="muted">{e.geocode_erro}</p>}
          {e.latitude !== null && <p><Link to={`/mapa?empresa=${e.id}`}>Ver no mapa</Link> · <a href={`https://www.openstreetmap.org/?mlat=${e.latitude}&mlon=${e.longitude}#map=18/${e.latitude}/${e.longitude}`} target="_blank" rel="noopener noreferrer">Abrir no OpenStreetMap</a></p>}
          {escreve && (<>
            <button className="btn btn-outline" onClick={novaGeo}>Pedir nova geocodificação</button>
            <form onSubmit={corrigirLocal} className="form-grid" style={{ marginTop: 12 }}>
              <label>Latitude<input value={la} onChange={(x) => setLa(x.target.value)} inputMode="decimal" placeholder="-7.9407" /></label>
              <label>Longitude<input value={lo} onChange={(x) => setLo(x.target.value)} inputMode="decimal" placeholder="-34.8728" /></label>
              <div className="full"><button className="btn btn-primary">Corrigir localização manualmente</button></div></form></>)}
          {msgLoc && <p role="status" className="muted">{msgLoc}</p>}</article>
        <article className="card"><h2>Informações adicionais</h2><p className="pre"><V v={e.informacoes_adicionais} /></p>
          {e.campos?.map((c) => <p key={c.chave}><strong>{c.rotulo}:</strong> {c.valor === null ? "—" : c.valor === true ? "Sim" : c.valor === false ? "Não" : String(c.valor)}</p>)}</article>
      </div>
      <article className="card"><h2>Histórico de Interações</h2>
        {escreve && (
          <form onSubmit={registrar} className="form-grid">
            <label>Tipo<select value={tipo} onChange={(x) => setTipo(x.target.value)}>{Object.entries(TIPOS_INTERACAO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label>Situação resultante<select value={sit} onChange={(x) => setSit(x.target.value)}><option value="">Manter</option>{sits.map((s) => <option key={s.id} value={s.id}>{s.nome}</option>)}</select></label>
            <label className="full">Descrição *<textarea rows={3} required maxLength={5000} value={desc} onChange={(x) => setDesc(x.target.value)} /></label>
            <label className="full">Observações<input value={obs} onChange={(x) => setObs(x.target.value)} maxLength={5000} /></label>
            {err && <p role="alert" className="error full">{err}</p>}
            <div className="full"><button className="btn btn-primary" disabled={!desc.trim()}>Registrar interação</button></div>
          </form>)}
        {hist.length === 0 ? <p className="muted">Nenhuma interação registrada.</p> : (
          <ol className="timeline">{hist.map((h) => (
            <li key={h.id}><strong>{fmtData(h.data_interacao)}</strong> · {TIPOS_INTERACAO[h.tipo]} · {h.usuario}
              <p className="pre">{h.descricao}</p>{h.observacoes && <p className="muted pre">{h.observacoes}</p>}
              {h.situacao_resultante && <span className="badge" style={{ background: "#215d98" }}>Situação: {h.situacao_resultante}</span>}</li>))}</ol>)}
      </article>
    </section>
  );
}
