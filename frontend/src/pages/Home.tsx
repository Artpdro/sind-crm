import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { TIPOS_INTERACAO } from "../types";

type Dist = { id: number | null; nome: string; cor: string | null; total: number };
type Painel = {
  total: number; com_localizacao: number; sem_localizacao: number; para_revisao: number; interacoes_30_dias: number;
  geocode: { localizado: number; pendente: number; nao_encontrado: number; revisao: number };
  por_cidade: Dist[]; por_situacao: Dist[]; por_porte: Dist[]; por_tipo: Dist[]; por_categoria: Dist[];
  empresas_recentes: { id: number; nome: string; cidade: string | null; created_at: string; situacao_nome: string | null }[];
  interacoes_recentes: { id: number; empresa_id: number; empresa: string; tipo: string; descricao: string; data_interacao: string; usuario: string }[];
};
const dataBr = (s: string) => new Date(s.replace(" ", "T") + "Z").toLocaleString("pt-BR", { timeZone: "America/Recife", dateStyle: "short", timeStyle: "short" });

function Lista({ titulo, dados }: { titulo: string; dados: Dist[] }) {
  const max = Math.max(1, ...dados.map((d) => d.total));
  return (
    <div className="card"><h2>{titulo}</h2>
      {dados.length === 0 ? <p className="muted">Sem dados.</p> : <ul className="barras">{dados.map((d) => (
        <li key={d.nome}><span className="barra-rot" title={d.nome}>{d.cor && <i className="pontinho" style={{ background: d.cor }} />}{d.nome}</span>
          <span className="barra-trilho"><span className="barra-valor" style={{ width: `${(d.total / max) * 100}%`, background: d.cor ?? undefined }} /></span><strong>{d.total}</strong></li>))}</ul>}
    </div>
  );
}

// O dashboard com estatísticas do banco entra na Fase 5; o Mapa na Fase 4; a Importação na Fase 3.
export default function Home() {
  const { user } = useAuth();
  const escreve = user?.role !== "visualizador";
  const [p, setP] = useState<Painel | null>(null); const [err, setErr] = useState("");
  useEffect(() => { api<Painel>("/api/dashboard").then(setP).catch((e) => setErr(e.message)); }, []);
  const cards: [string, string, string | null][] = [
    ["Relatórios", "PDF e resumo de atividades por data.", "/relatorios"],
    ["Empresas", "Lista com busca, filtros e paginação.", "/empresas"],
    ["Consultar empresa", "Busque pelo CNPJ.", "/consultar"],
    ["Cadastrar empresa", "Cadastro manual.", escreve ? "/empresas/nova" : null],
    ["Cadastros", "Situações, categorias, portes e tipos.", user?.role === "admin" ? "/cadastros" : null],
    ["Importar empresas", "Planilha Excel (.xls ou .xlsx) com prévia.", user?.role === "admin" ? "/importar" : null], ["Mapa de Empresas", "Pesquise, filtre e veja as empresas no mapa.", "/mapa"], ["Localização", "Geocodificação e correções.", escreve ? "/localizacao" : null],
  ];
  return (
    <section>
      <div className="hero"><h1>Olá, {user?.username}</h1><p>Sistema de gestão e mapa de empresas do Sindnorte.</p></div>
      {err && <p role="alert" className="error">{err}</p>}
      {p && (
        <>
          <div className="tiles">
            <div className="tile"><b>{p.total}</b><span>empresas cadastradas</span></div>
            <div className="tile"><b>{p.com_localizacao}</b><span>no mapa · {p.sem_localizacao} sem localização</span></div>
            <div className="tile"><b>{p.para_revisao}</b><span>cadastros para revisão</span></div>
            <div className="tile"><b>{p.interacoes_30_dias}</b><span>atividades nos últimos 30 dias</span></div>
          </div>
          {(p.geocode.pendente > 0 || p.geocode.revisao > 0 || p.geocode.nao_encontrado > 0) &&
            <p className="muted">Localização: {p.geocode.localizado} localizadas · {p.geocode.pendente} pendentes · {p.geocode.revisao} para revisão · {p.geocode.nao_encontrado} não encontradas. {escreve && <Link to="/localizacao">Abrir Localização</Link>}</p>}
          <div className="grid2">
            <Lista titulo="Por situação" dados={p.por_situacao} />
            <Lista titulo="Por cidade" dados={p.por_cidade} />
            <Lista titulo="Por porte" dados={p.por_porte} />
            <Lista titulo="Por tipo de empresa" dados={p.por_tipo} />
            <Lista titulo="Por categoria" dados={p.por_categoria} />
            <div className="card"><h2>Cadastradas recentemente</h2>
              <ul className="lista">{p.empresas_recentes.map((e) => <li key={e.id} className="item-ativ"><Link to={`/empresas/${e.id}`}>{e.nome}</Link><br /><small className="muted">{[e.cidade, e.situacao_nome, dataBr(e.created_at)].filter(Boolean).join(" · ")}</small></li>)}</ul></div>
          </div>
          <div className="card"><h2>Atividades recentes <Link to="/relatorios" className="muted" style={{ fontSize: ".85rem", fontWeight: 400 }}>— relatório por data</Link></h2>
            {p.interacoes_recentes.length === 0 ? <p className="muted">Nenhuma atividade registrada ainda.</p> :
              <ul className="lista">{p.interacoes_recentes.map((i) => <li key={i.id} className="item-ativ"><strong>{dataBr(i.data_interacao)}</strong> · {TIPOS_INTERACAO[i.tipo] ?? i.tipo} · {i.usuario}<br />
                <Link to={`/empresas/${i.empresa_id}`}>{i.empresa}</Link> — {i.descricao}</li>)}</ul>}
          </div>
        </>)}
      <div className="grid">
        {cards.filter(([, , to]) => to || true).map(([t, d, to]) => to
          ? <Link key={t} to={to} className="card card-link"><h2>{t}</h2><p className="muted">{d}</p></Link>
          : <article key={t} className="card off"><h2>{t}</h2><p className="muted">Indisponível nesta fase.</p></article>)}
      </div>
    </section>
  );
}
