import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiUpload, ApiError } from "../api";
import type { Importacao, LinhaPrevia } from "../types";

const ACOES: [string, string][] = [["", "Todas"], ["novo", "Novas"], ["atualizar", "Para atualizar"], ["duplicada", "Duplicadas (descartadas)"],
  ["rejeitada", "Rejeitadas"], ["sem_alteracao", "Sem alteração"], ["ja_existente", "Já existentes (sem CNPJ)"]];
const ROTULO: Record<string, string> = { novo: "Nova", atualizar: "Atualizar", sem_alteracao: "Sem alteração", duplicada: "Duplicada", rejeitada: "Rejeitada", ja_existente: "Já existe" };
type Modo = "novos" | "atualizar" | "ambos";

export default function Importar() {
  const [imp, setImp] = useState<Importacao | null>(null); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const [modo, setModo] = useState<Modo>("novos"); const [filtro, setFiltro] = useState(""); const [revisao, setRevisao] = useState(false);
  const [page, setPage] = useState(1); const [linhas, setLinhas] = useState<{ total: number; itens: LinhaPrevia[] } | null>(null);

  const emPrevia = imp?.status === "previa";
  const carregar = useCallback(async () => {
    if (!imp || !emPrevia) return;
    const qs = new URLSearchParams({ page: String(page), page_size: "50" });
    if (filtro) qs.set("acao", filtro); if (revisao) qs.set("somente_revisao", "true");
    try { setLinhas(await api(`/api/importacoes/${imp.id}/linhas?${qs}`)); } catch (e) { setErr((e as Error).message); }
  }, [imp, emPrevia, page, filtro, revisao]);
  useEffect(() => { void carregar(); }, [carregar]);

  async function enviar(ev: React.ChangeEvent<HTMLInputElement>) {
    const f = ev.target.files?.[0]; ev.target.value = ""; if (!f) return;
    setErr(""); setImp(null); setLinhas(null); setPage(1); setFiltro(""); setRevisao(false); setBusy(true);
    try { const fd = new FormData(); fd.append("arquivo", f); setImp(await apiUpload<Importacao>("/api/importacoes", fd)); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); } finally { setBusy(false); }
  }
  async function confirmar() {
    if (!imp) return;
    const r = imp.resumo;
    const txt = { novos: `Cadastrar ${r.novas} empresas novas?`, atualizar: `Atualizar ${r.para_atualizar} empresas existentes?`,
      ambos: `Cadastrar ${r.novas} novas e atualizar ${r.para_atualizar} existentes?` }[modo];
    if (!confirm(txt + "\\n\\nEsta operação não pode ser desfeita.")) return;
    setBusy(true); setErr("");
    try { setImp(await api<Importacao>(`/api/importacoes/${imp.id}/confirmar`, { method: "POST", body: { modo } })); setLinhas(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Falha de conexão."); } finally { setBusy(false); }
  }
  async function cancelar() {
    if (!imp) return;
    try { setImp(await api<Importacao>(`/api/importacoes/${imp.id}/cancelar`, { method: "POST" })); setLinhas(null); } catch (e) { setErr((e as Error).message); }
  }

  const r = imp?.resumo;
  const cards: [string, number | undefined, string][] = [["Novas", r?.novas, "novo"], ["Para atualizar", r?.para_atualizar, "atualizar"],
    ["Sem alteração", r?.sem_alteracao, "sem_alteracao"], ["Já existentes (sem CNPJ)", r?.ja_existentes_sem_cnpj, "ja_existente"],
    ["Duplicadas descartadas", r?.duplicadas_descartadas, "duplicada"], ["Rejeitadas", r?.rejeitadas, "rejeitada"]];
  const paginas = linhas ? Math.max(1, Math.ceil(linhas.total / 50)) : 1;

  return (
    <section>
      <h1>Importar empresas</h1>
      <p className="muted">Envie a planilha do Excel (<strong>.xls</strong> ou <strong>.xlsx</strong>, até 5 MB) com as colunas: Nome, Email, Emails adicionais, Celular, Empresa, CPF ou CNPJ, Fone, Rua, Número, Complemento, Bairro, Cidade, CEP, Estado. Outras colunas (como Identificador externo e Valor vencido) são ignoradas. Nada é gravado antes da sua confirmação.</p>
      {(!imp || !emPrevia) && (
        <div className="card"><label>Arquivo .xls ou .xlsx<input type="file" accept=".xls,.xlsx" onChange={enviar} disabled={busy} /></label>
          {busy && <p className="muted">Processando…</p>}</div>)}
      {err && <p role="alert" className="error">{err}</p>}

      {imp && emPrevia && r && (<>
        <div className="card"><h2>Prévia — {imp.nome_arquivo}</h2>
          <p className="muted">{r.linhas_lidas} linhas lidas{r.linhas_em_branco ? ` · ${r.linhas_em_branco} em branco ignoradas` : ""}.
            {r.novas_para_revisao > 0 && <> <strong>{r.novas_para_revisao}</strong> das novas entrarão <strong>marcadas para revisão</strong> (sem CNPJ, CNPJ incompleto, endereço incompleto ou dado duvidoso).</>}</p>
          {r.cabecalhos_ignorados.length > 0 && <p className="aviso">Colunas ignoradas: {r.cabecalhos_ignorados.join(", ")}.</p>}
          {r.avisos.map((a) => <p key={a} className="aviso">{a}</p>)}
          <div className="grid">{cards.map(([t, n, a]) => (
            <button key={a} className={"card stat" + (filtro === a ? " on" : "")} onClick={() => { setFiltro(filtro === a ? "" : a); setPage(1); }}><strong>{n ?? 0}</strong><span>{t}</span></button>))}</div>
        </div>
        <div className="card"><h2>O que fazer?</h2>
          <fieldset className="modos">
            {([["novos", "Cadastrar novos registros", "Só cria as empresas que ainda não existem. As existentes ficam como estão."],
               ["atualizar", "Atualizar registros existentes", "Só atualiza as que já existem (pelo CNPJ). Células vazias nunca apagam dados."],
               ["ambos", "Cadastrar novos e atualizar existentes", "Faz as duas coisas."]] as [Modo, string, string][]).map(([v, t, d]) => (
              <label key={v} className="check modo"><input type="radio" name="modo" checked={modo === v} onChange={() => setModo(v)} /><span><strong>{t}</strong><br /><small className="muted">{d}</small></span></label>))}
          </fieldset>
          <div className="row"><button className="btn btn-primary" onClick={confirmar} disabled={busy}>{busy ? "Importando…" : "Confirmar importação"}</button>
            <button className="btn btn-outline" onClick={cancelar} disabled={busy}>Cancelar</button></div></div>
        <div className="card"><div className="row"><h2>Linhas</h2>
          <label className="check"><input type="checkbox" checked={revisao} onChange={(e) => { setRevisao(e.target.checked); setPage(1); }} />Somente marcadas para revisão</label></div>
          <div className="tabs">{ACOES.map(([k, t]) => <button key={k} className={filtro === k ? "tab on" : "tab"} onClick={() => { setFiltro(k); setPage(1); }}>{t}</button>)}</div>
          <p className="muted" aria-live="polite">{linhas ? `${linhas.total} linhas` : "Carregando…"}</p>
          <div className="table-wrap"><table><thead><tr><th>Linha</th><th>Ação</th><th>Empresa</th><th>Detalhes</th></tr></thead><tbody>
            {linhas?.itens.map((l) => (<tr key={l.linha}><td>{l.linha}</td><td>{ROTULO[l.acao]}{l.revisao && <span className="badge" style={{ background: "#b45309", marginLeft: 6 }}>Revisar</span>}</td>
              <td>{l.nome ?? "—"}<br /><small className="muted">{l.cnpj ?? "sem CNPJ"}{l.cidade ? ` · ${l.cidade}` : ""}</small></td>
              <td><small>{[...l.erros.map((e) => "Erro: " + e), ...(l.mantida_linha ? [`Descartada: mantida a linha ${l.mantida_linha}, mais completa.`] : []),
                ...(l.acao === "atualizar" ? ["Atualiza: " + l.alteracoes.join(", ")] : []), ...l.motivos, ...l.avisos].map((m, i) => <span key={i}>{m}<br /></span>)}</small></td></tr>))}
          </tbody></table></div>
          <div className="row pager"><button className="btn btn-outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</button><span>Página {page} de {paginas}</span>
            <button className="btn btn-outline" disabled={page >= paginas} onClick={() => setPage(page + 1)}>Próxima</button></div></div>
      </>)}

      {imp?.status === "concluida" && imp.resultado && (
        <div className="card"><h2>Importação concluída</h2>
          <ul><li><strong>{imp.resultado.criadas}</strong> empresas criadas{imp.resultado.criadas_para_revisao ? ` (${imp.resultado.criadas_para_revisao} marcadas para revisão)` : ""}</li>
            <li><strong>{imp.resultado.atualizadas}</strong> atualizadas · {imp.resultado.sem_alteracao} sem alteração · {imp.resultado.ja_existentes} já existiam (sem CNPJ)</li>
            <li>{imp.resultado.duplicadas_descartadas} duplicadas descartadas · {imp.resultado.rejeitadas} rejeitadas · {imp.resultado.ignoradas_pelo_modo} ignoradas pelo modo escolhido</li></ul>
          <p className="muted">As novas empresas ficam com a localização <em>pendente</em> até a geocodificação (próxima fase).</p>
          <div className="row"><Link className="btn btn-primary" to="/empresas?revisao=true">Ver empresas para revisão</Link>
            <button className="btn btn-outline" onClick={() => { setImp(null); setLinhas(null); }}>Importar outro arquivo</button></div></div>)}
      {imp && ["cancelada", "falhou"].includes(imp.status) && (
        <div className="card"><h2>{imp.status === "cancelada" ? "Importação cancelada" : "A importação falhou"}</h2><p className="muted">Nenhuma empresa foi alterada.</p>
          <button className="btn btn-outline" onClick={() => setImp(null)}>Importar outro arquivo</button></div>)}
    </section>
  );
}
