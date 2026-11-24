// O token CSRF fica só em memória (nunca em localStorage). Após recarregar a página,
// /api/auth/me devolve um token válido para a sessão atual (o cookie HttpOnly segue o navegador).
let csrf = "";
export const setCsrf = (t: string) => { csrf = t; };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const res = await fetch(path, {
    method, headers, credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let detail = typeof data.detail === "string" ? data.detail : "Não foi possível concluir a operação.";
    if (Array.isArray(data.erros) && data.erros.length)  // erros de validação: campo + motivo (nunca o valor enviado)
      detail += " " + data.erros.map((e: { campo: string; erro: string }) => `${e.campo}: ${e.erro}`).join("; ");
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export async function apiUpload<T = unknown>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", headers: { "X-CSRF-Token": csrf }, credentials: "same-origin", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : "Não foi possível enviar o arquivo.");
  return data as T;
}

/** Baixa um arquivo (PDF) gerado pelo servidor. Erros do servidor (ex.: "refine a busca") viram ApiError com a mensagem. */
export async function baixar(path: string, nomePadrao = "relatorio.pdf"): Promise<void> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : "Não foi possível gerar o PDF.");
  }
  const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = m?.[1] ?? nomePadrao;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
