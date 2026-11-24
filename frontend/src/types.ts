export type Opcao = { id: number; nome: string; ativa: boolean; cor?: string; ordem?: number };
export type Campo = { id: number; chave: string; rotulo: string; tipo: "booleano" | "data" | "texto" | "numero" | "selecao"; opcoes: string[] | null; obrigatorio: boolean };
export type Empresa = {
  id: number; version: number; nome: string; nome_fantasia: string | null; cnpj: string | null;
  email: string | null; emails_adicionais: string[]; celular: string | null; fone: string | null;
  cep: string | null; rua: string | null; numero: string | null; complemento: string | null; bairro: string | null;
  cidade: string | null; estado: string | null; situacao_id: number | null; situacao_nome: string | null; situacao_cor: string | null;
  porte_id: number | null; porte_nome: string | null; tipo_empresa_id: number | null; tipo_nome: string | null;
  informacoes_adicionais: string | null; latitude: number | null; longitude: number | null; geocode_status: string;
  geocode_desatualizada: boolean; precisa_revisao: boolean; motivos_revisao: string[]; geocode_precisao: string | null; geocode_fonte?: string | null; geocode_erro: string | null; cnpj_original: string | null; categorias: { id: number; nome: string }[];
  campos?: { chave: string; rotulo: string; tipo: string; valor: string | number | boolean | null }[];
};
export type Interacao = { id: number; tipo: string; descricao: string; observacoes: string | null; data_interacao: string; usuario: string; situacao_resultante: string | null };
export const TIPOS_INTERACAO: Record<string, string> = { ligacao: "Ligação", whatsapp: "WhatsApp", email: "Email", visita: "Visita", reuniao: "Reunião", atendimento: "Atendimento", outro: "Outro" };
export const GEO_STATUS: Record<string, string> = { localizado: "Localizado", pendente: "Localização pendente", nao_encontrado: "Endereço não encontrado", revisao: "Localização precisa de revisão" };
export const fmtCnpj = (c: string | null) => (c && c.length === 14 ? `${c.slice(0, 2)}.${c.slice(2, 5)}.${c.slice(5, 8)}/${c.slice(8, 12)}-${c.slice(12)}` : c ?? "—");
export const fmtData = (s: string) => { const d = new Date(s.replace(" ", "T") + "Z"); return isNaN(+d) ? s : d.toLocaleString("pt-BR"); };
export const endereco = (e: Empresa) => [[e.rua, e.numero].filter(Boolean).join(", "), e.complemento, e.bairro, [e.cidade, e.estado].filter(Boolean).join(" - "), e.cep].filter(Boolean).join(" · ") || "—";

export type Resumo = { linhas_lidas: number; linhas_em_branco: number; novas: number; novas_para_revisao: number; para_atualizar: number;
  sem_alteracao: number; ja_existentes_sem_cnpj: number; duplicadas_descartadas: number; rejeitadas: number; cabecalhos_ignorados: string[]; avisos: string[] };
export type Resultado = { criadas: number; criadas_para_revisao: number; atualizadas: number; sem_alteracao: number; ja_existentes: number;
  ignoradas_pelo_modo: number; rejeitadas: number; duplicadas_descartadas: number };
export type Importacao = { id: number; status: string; nome_arquivo: string; modo: string | null; resumo: Resumo; resultado: Resultado | null };
export type LinhaPrevia = { linha: number; acao: string; revisao: boolean; nome: string | null; cnpj: string | null; cidade: string | null;
  motivos: string[]; avisos: string[]; erros: string[]; alteracoes: string[]; mantida_linha: number | null };

export type Marcador = { id: number; lat: number; lon: number; cor: string; revisao: boolean; desatualizada: boolean; aprox: boolean };
export type MapaResp = { total: number; com_localizacao: number; sem_localizacao: number; excluidos_sem_numero?: number; truncado: boolean; marcadores: Marcador[] };
export type Bbox = { sul: number; oeste: number; norte: number; leste: number };
export type CampoFiltro = { id: number; chave: string; rotulo: string; tipo: "booleano" | "data" | "texto" | "numero" | "selecao"; opcoes: string[] | null };
export type ResumoGeo = { automatica: boolean; estimativa_min: number; localizado: number; pendente: number; nao_encontrado: number; revisao: number; desatualizadas: number; total: number; contato_configurado: boolean; servico: { ultimo_erro: string | null; ultimo_erro_em: string | null; ultimo_ok_em: string | null; consultas: number } };
export type Diagnostico = { por_fonte?: Record<string, number>; por_precisao: Record<string, number>; motivos: { status: string; motivo: string; quantidade: number }[]; sem_rua_ou_cidade: number;
  exemplos_nao_encontradas: { id: number; rua: string | null; numero: string | null; bairro: string | null; cidade: string | null; cep: string | null; motivo: string | null }[]; resumo: ResumoGeo };
export const PRECISAO: Record<string, string> = { endereco: "endereço exato", interpolado: "estimada entre vizinhos (aproximada)", rua: "pela rua (aproximada)", cep: "pelo CEP (aproximada)", bairro: "pelo bairro (aproximada)", manual: "manual", "sem coordenada": "sem coordenada" };
