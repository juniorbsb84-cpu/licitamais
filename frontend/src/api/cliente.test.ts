import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { servidor } from "../testes/servidor";
import { api, destinoVolta, ErroApi, definirCsrf } from "./cliente";

beforeAll(() => servidor.listen()); afterEach(() => servidor.resetHandlers()); afterAll(() => servidor.close());

test("GET monta query com lista repetida", async () => {
  let url = "";
  servidor.use(http.get("/api/v2/licitacoes", ({ request }) => { url = request.url; return HttpResponse.json({ ok: 1 }); }));
  await api("/licitacoes", { params: { q: "pneu", situacao: ["aberta", "andamento"], pagina: 2, vazio: undefined } });
  expect(new URL(url).search).toBe("?q=pneu&situacao=aberta&situacao=andamento&pagina=2");
});

test("POST leva X-CSRF", async () => {
  let csrf: string | null = null;
  servidor.use(http.post("/api/v2/alertas", ({ request }) => { csrf = request.headers.get("X-CSRF"); return HttpResponse.json({}, { status: 201 }); }));
  definirCsrf("abc");
  await api("/alertas", { metodo: "POST", corpo: { palavras: ["pneus"] } });
  expect(csrf).toBe("abc");
});

test("401 fora do entrar manda para /entrar sem volta (volta aninhava a URL em loop)", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, pathname: "/licitacao/3", search: "", assign });
  // r53: quem leva ao login e a rota de dados; /me 401 so indica visitante (ver teste abaixo)
  servidor.use(http.get("/api/v2/licitacoes/3", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })));
  await expect(api("/licitacoes/3")).rejects.toBeInstanceOf(ErroApi);
  expect(assign).toHaveBeenCalledWith("/entrar");
  vi.unstubAllGlobals();
});

test("401 no entrar nao redireciona em loop", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, pathname: "/entrar", search: "", assign });
  servidor.use(http.get("/api/v2/me", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })));
  await expect(api("/me")).rejects.toBeInstanceOf(ErroApi);
  expect(assign).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

test("erro da API vira ErroApi com a mensagem do servidor", async () => {
  servidor.use(http.get("/api/v2/licitacoes", () => HttpResponse.json({ erro: "parâmetros inválidos" }, { status: 422 })));
  await expect(api("/licitacoes")).rejects.toMatchObject({ status: 422, message: "parâmetros inválidos" });
});

test("401 guarda a rota para voltar depois do login, so se for caminho interno", async () => {
  const assign = vi.fn();
  sessionStorage.clear();
  vi.stubGlobal("location", { ...window.location, pathname: "/", search: "?q=pneu&fonte=senac&pagina=4", assign });
  servidor.use(http.get("/api/v2/licitacoes", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })));
  await expect(api("/licitacoes")).rejects.toBeInstanceOf(ErroApi);
  expect(sessionStorage.getItem("volta")).toBe("/?q=pneu&fonte=senac&pagina=4");
  vi.unstubAllGlobals();
});

test.each(["//evil.com/x", "https://evil.com", "/entrar?x=1", "javascript:alert(1)", ""])("destinoVolta rejeita %s", (v) => {
  expect(destinoVolta(v)).toBeNull();
});

test("destinoVolta aceita caminho interno", () => {
  expect(destinoVolta("/licitacao/3")).toBe("/licitacao/3");
});

test("401 em /me nao redireciona: no site aberto o visitante continua lendo", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, pathname: "/", search: "", assign });
  servidor.use(http.get("/api/v2/me", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })));
  await expect(api("/me")).rejects.toBeInstanceOf(ErroApi);
  expect(assign).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});
