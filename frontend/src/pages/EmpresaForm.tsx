import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { Campo, Empresa, Opcao } from "../types";

type F = Record<string, string>;
const VAZIO: F = { nome: "", nome_fantasia: "", cnpj: "", email: "", emails_adicionais: "", celular: "", fone: "", cep: "", rua: "", numero: "",
  complemento: "", bairro: "", cidade: "", estado: "PE", porte_id: "", tipo_empresa_id: "", situacao_id: "", informacoes_adicionais: "" };
const TEXTO: [string, string, string?][] = [["nome", "Nome da Empresa *"], ["nome_fantasia", "Nome Fantasia"], ["cnpj", "CNPJ"], ["email", "Email", "email"],
  ["emails_adicionais", "Emails adicionais (separe por vírgula)"], ["celular", "Celular"], ["fone", "Fone"], ["cep", "CEP"], ["rua", "Rua"], ["numero", "Número"],
  ["complemento", "Complemento"], ["bairro", "Bairro"], ["cidade", "Cidade"], ["estado", "Estado (UF)"]];

export default function EmpresaForm() {
  const { id } = useParams(); const nav = useNavigate(); const [sp] = useSearchParams();
  const [f, setF] = useState<F>({ ...VAZIO, cnpj: sp.get("cnpj") ?? "" });
  const [cats, setCats] = useState<number[]>([]); const [campos, setCampos] = useState<Record<string, string | boolean>>({});
  const [version, setVersion] = useState(0); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const [L, setL] = useState<{ sit: Opcao[]; porte: Opcao[]; tipo: Opcao[]; cat: Opcao[]; campos: Campo[] }>({ sit: [], porte: [], tipo: [], cat: [], campos: [] });

  useEffect(() => {
    Promise.all([api<Opcao[]>("/api/cadastros/situacoes"), api<Opcao[]>("/api/cadastros/portes"), api<Opcao[]>("/api/cadastros/tipos-empresa"),
      api<Opcao[]>("/api/cadastros/categorias"), api<Campo[]>("/api/campos")]).then(([sit, porte, tipo, cat, cs]) => setL({ sit, porte, tipo, cat, campos: cs })).catch((e) => setErr(e.message));
  }, []);
  useEffect(() => {
    if (!id) return;
    api<Empresa>(`/api/empresas/${id}`).then((e) => {
      setVersion(e.version); setCats(e.categorias.map((c) => c.id));
      setF({ ...VAZIO, ...Object.fromEntries(Object.entries(e).filter(([k]) => k in VAZIO).map(([k, v]) => [k, v == null ? "" : String(v)])), emails_adicionais: e.emails_adicionais.join(", ") });
      setCampos(Object.fromEntries((e.campos ?? []).filter((c) => c.valor !== null).map((c) => [c.chave, typeof c.valor === "boolean" ? c.valor : String(c.valor)])));
    }).catch((x) => setErr(x.message));
  }, [id]);

  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  async function salvar(ev: FormEvent) {
    ev.preventDefault(); setErr(""); setBusy(true);
    const n = (k: string) => (f[k].trim() === "" ? null : f[k].trim());
    const corpo: Record<string, unknown> = { nome: n("nome"), nome_fantasia: n("nome_fantasia"), cnpj: n("cnpj"), email: n("email"),
      emails_adicionais: f.emails_adicionais.split(",").map((s) => s.trim()).filter(Boolean), celular: n("celular"), fone: n("fone"), cep: n("cep"),
      rua: n("rua"), numero: n("numero"), complemento: n("complemento"), bairro: n("bairro"), cidade: n("cidade"), estado: n("estado"),
      porte_id: f.porte_id ? +f.porte_id : null, tipo_empresa_id: f.tipo_empresa_id ? +f.tipo_empresa_id : null, situacao_id: f.situacao_id ? +f.situacao_id : null,
      informacoes_adicionais: n("informacoes_adicionais"), categoria_ids: cats };
    const cs: Record<string, unknown> = {};
    for (const c of L.campos) {
      const v = campos[c.chave];
      cs[c.chave] = v === undefined || v === "" ? null : c.tipo === "numero" ? Number(v) : v;
    }
    corpo.campos = cs;
    try {
      const r = id ? await api<Empresa>(`/api/empresas/${id}`, { method: "PATCH", body: { ...corpo, version } })
                   : await api<Empresa>("/api/empresas", { method: "POST", body: corpo });
      nav(`/empresas/${r.id}`);
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Falha de conexão."); } finally { setBusy(false); }
  }
  const toggleCat = (cid: number) => setCats(cats.includes(cid) ? cats.filter((c) => c !== cid) : [...cats, cid]);
  const sel = (k: string, rotulo: string, ops: Opcao[]) => (
    <label key={k}>{rotulo}<select value={f[k]} onChange={set(k)}><option value="">—</option>{ops.map((o) => <option key={o.id} value={o.id}>{o.nome}</option>)}</select></label>);

  return (
    <section>
      <h1>{id ? "Editar empresa" : "Cadastrar empresa"}</h1>
      <form className="card form-grid" onSubmit={salvar}>
        {TEXTO.map(([k, r, t]) => <label key={k}>{r}<input type={t ?? "text"} value={f[k]} onChange={set(k)} required={k === "nome"} maxLength={k === "estado" ? 2 : 300} /></label>)}
        {sel("porte_id", "Porte", L.porte)}{sel("tipo_empresa_id", "Tipo de Empresa", L.tipo)}{sel("situacao_id", "Situação", L.sit)}
        <fieldset className="full"><legend>Categorias</legend>
          {L.cat.length === 0 && <span className="muted">Nenhuma categoria cadastrada.</span>}
          {L.cat.map((c) => <label key={c.id} className="check"><input type="checkbox" checked={cats.includes(c.id)} onChange={() => toggleCat(c.id)} />{c.nome}</label>)}
        </fieldset>
        <label className="full">Informações adicionais<textarea rows={4} value={f.informacoes_adicionais} onChange={set("informacoes_adicionais")} maxLength={20000} /></label>
        {L.campos.map((c) => (
          <label key={c.chave} className={c.tipo === "booleano" ? "check" : ""}>{c.tipo === "booleano" ? "" : c.rotulo + (c.obrigatorio ? " *" : "")}
            {c.tipo === "booleano" ? <><input type="checkbox" checked={campos[c.chave] === true} onChange={(e) => setCampos({ ...campos, [c.chave]: e.target.checked })} />{c.rotulo}</>
              : c.tipo === "selecao" ? <select value={String(campos[c.chave] ?? "")} onChange={(e) => setCampos({ ...campos, [c.chave]: e.target.value })}><option value="">—</option>{c.opcoes?.map((o) => <option key={o}>{o}</option>)}</select>
              : <input type={c.tipo === "data" ? "date" : c.tipo === "numero" ? "number" : "text"} value={String(campos[c.chave] ?? "")} onChange={(e) => setCampos({ ...campos, [c.chave]: e.target.value })} />}
          </label>))}
        {err && <p role="alert" className="error full">{err}</p>}
        <div className="full row"><button className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : "Salvar"}</button>
          <button type="button" className="btn btn-outline" onClick={() => nav(-1)}>Cancelar</button></div>
      </form>
    </section>
  );
}
