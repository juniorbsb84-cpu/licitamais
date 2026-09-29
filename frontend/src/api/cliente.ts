export class ErroApi extends Error {
  status: number;
  constructor(status: number, mensagem: string) { super(mensagem); this.status = status; }
}

let csrf = "";
export const definirCsrf = (valor: string) => { csrf = valor; };

/** Caminho interno seguro para voltar apos o login; qualquer outra coisa vira null (sem open redirect). */
export function destinoVolta(valor: string | null): string | null {
  if (!valor || !valor.startsWith("/") || valor.startsWith("//") || valor.startsWith("/\\")) return null;
  if (valor === "/entrar" || valor.startsWith("/entrar?") || valor.startsWith("/api/")) return null;
  return valor;
}

type Params = Record<string, string | string[] | number | undefined>;

function query(params?: Params): string {
  const busca = new URLSearchParams();
  for (const [chave, valor] of Object.entries(params ?? {})) {
    if (valor === undefined || valor === "") continue;
    for (const v of Array.isArray(valor) ? valor : [valor]) busca.append(chave, String(v));
  }
  const texto = busca.toString();
  return texto ? `?${texto}` : "";
}

export async function api<T>(caminho: string, opcoes: { metodo?: "GET" | "POST" | "DELETE"; corpo?: unknown; params?: Params } = {}): Promise<T> {
  const metodo = opcoes.metodo ?? "GET";
  const resposta = await fetch(`/api/v2${caminho}${query(opcoes.params)}`, {
    method: metodo,
    credentials: "same-origin",
    headers: {
      ...(opcoes.corpo !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(metodo !== "GET" ? { "X-CSRF": csrf } : {}),
    },
    body: opcoes.corpo !== undefined ? JSON.stringify(opcoes.corpo) : undefined,
  });
  // /me 401 so diz "visitante": o site aberto deixa ler sem conta; quem exige conta e a rota de dados
  if (resposta.status === 401 && caminho !== "/auth/link" && caminho !== "/me") {
    // a rota vai para o sessionStorage, nao para a URL: ?volta= aninhava a URL em loop
    if (location.pathname !== "/entrar") {
      const volta = destinoVolta(location.pathname + location.search);
      try { if (volta) sessionStorage.setItem("volta", volta); } catch { /* navegador sem storage: volta para "/" */ }
      window.location.assign("/entrar");
    }
  }
  if (!resposta.ok) {
    const corpo = await resposta.json().catch(() => ({}));
    throw new ErroApi(resposta.status, (corpo as { erro?: string }).erro ?? "Não foi possível falar com o servidor.");
  }
  return (resposta.status === 204 ? undefined : await resposta.json()) as T;
}
