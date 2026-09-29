import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Oportunidades } from "./Oportunidades";
import { servidor } from "../testes/servidor";
import licitacoes from "../testes/dados/amostra_licitacoes.json";
import resumo from "../testes/dados/amostra_resumo.json";

vi.mock("../api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: false, csrf: "x" } }),
}));

beforeAll(() => servidor.listen());
afterEach(() => { servidor.resetHandlers(); vi.useRealTimers(); });
afterAll(() => servidor.close());

function padrao() {
  servidor.use(
    http.get("/api/v2/resumo", () => HttpResponse.json(resumo)),
    http.get("/api/v2/licitacoes", () => HttpResponse.json(licitacoes)),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
}

function montar(rota = "/") {
  const id = `tela-${Math.random().toString(36).slice(2)}`;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[rota]}>
        <Routes><Route path="/" element={<div data-testid={id}><Oportunidades /></div>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByTestId(id);
}

function textoResumo(el: HTMLElement): string {
  return (el.textContent ?? "").replace(/\s+/g, " ");
}

test("mostra o resumo de abertas e fechando", async () => {
  padrao();
  const tela = montar();
  expect(await within(tela).findByRole("heading", { level: 1, name: "262 licitações abertas onde o PNCP não chega." })).toBeInTheDocument();
  expect(textoResumo(tela)).toContain("24 fecham nos próximos 7 dias.");
});

test("régua de prazos e quadro de evidência vêm da API", async () => {
  padrao();
  const prazos = Array.from({ length: 14 }, (_, i) => ({ data: `2026-09-${String(25 + (i % 5)).padStart(2, "0")}`, qtd: i === 1 ? 3 : 0 }));
  let pedidos = "";
  servidor.use(
    http.get("/api/v2/resumo", () => HttpResponse.json({ ...resumo, prazos })),
    http.get("/api/v2/evidencias", ({ request }) => {
      pedidos = new URL(request.url).search;
      const ids = new URL(request.url).searchParams.getAll("ids").map(Number);
      return HttpResponse.json(ids.map((id, k) => k === 0
        ? { id, valor_estimado: null, parecidas: 4, desde: 2019, valor_mediano: 15650, desconto_medio: 0.186, mais_venceu: "CHECON DESIGN LTDA" }
        : { id, valor_estimado: null, parecidas: 0, desde: null, valor_mediano: null, desconto_medio: null, mais_venceu: null }));
    }),
  );
  const tela = montar();
  expect(await within(tela).findByText("Sexta-feira, 25 de setembro de 2026")).toBeInTheDocument();
  expect(within(tela).getByLabelText("26 de setembro: 3 aberturas")).toBeInTheDocument();
  expect(await within(tela).findByText("4 desde 2019")).toBeInTheDocument();
  expect(textoResumo(tela)).toContain("18,6%");
  expect(within(tela).getAllByText("Sem compra parecida no histórico.").length).toBeGreaterThan(0);
  expect(pedidos).toContain("ids=");
});

test("renderiza o grupo sem data e linhas com prazo urgente", async () => {
  padrao();
  const tela = montar();
  expect(await within(tela).findByRole("heading", { name: /Sem data na fonte/ })).toBeInTheDocument();
  expect((await within(tela).findAllByRole("link", { name: /SEST\/SENAT|SENAC/i })).length).toBeGreaterThan(0);
});

test("clicar no filtro SENAC refaz a busca com fonte=senac", async () => {
  padrao();
  let ultima = "";
  servidor.use(http.get("/api/v2/licitacoes", ({ request }) => {
    ultima = new URL(request.url).search;
    return HttpResponse.json(licitacoes);
  }));
  const tela = montar();
  await within(tela).findByRole("heading", { name: /Sem data na fonte/ });
  const usuario = userEvent.setup();
  await usuario.click(within(tela).getByRole("checkbox", { name: /SENAC/ }));
  await vi.waitFor(() => expect(ultima).toContain("fonte=senac"));
});

test("busca com debounce so envia a ultima", async () => {
  padrao();
  const consultas: string[] = [];
  servidor.use(http.get("/api/v2/licitacoes", ({ request }) => {
    consultas.push(new URL(request.url).searchParams.get("q") ?? "");
    return HttpResponse.json({ ...(licitacoes as object), itens: [] });
  }));
  const tela = montar();
  await vi.waitFor(() => expect(consultas.length).toBeGreaterThan(0));
  consultas.length = 0;
  const usuario = userEvent.setup();
  const campo = within(tela).getByRole("searchbox", { name: /Buscar/i });
  await usuario.type(campo, "pn");
  await usuario.type(campo, "eu");
  await vi.waitFor(() => expect(consultas).toContain("pneu"), { timeout: 3000 });
  await new Promise((r) => setTimeout(r, 400));
  expect(consultas.filter((q) => q !== "" && q !== "pneu")).toHaveLength(0);
  expect(consultas[consultas.length - 1]).toBe("pneu");
});

test("422 mostra mensagem com botao limpar", async () => {
  padrao();
  servidor.use(http.get("/api/v2/licitacoes", () => HttpResponse.json({ erro: "parâmetros inválidos" }, { status: 422 })));
  montar("/?situacao=xyz");
  expect(await screen.findByText("Algum filtro está inválido. Limpe os filtros e tente de novo.")).toBeInTheDocument();
  expect(within(screen.getByTestId("erro-lista")).getByRole("button", { name: "Limpar filtros" })).toBeInTheDocument();
});

test("lista vazia convida a limpar os filtros", async () => {
  padrao();
  servidor.use(http.get("/api/v2/licitacoes", () => HttpResponse.json({ ...(licitacoes as object), itens: [], total: 0 })));
  montar("/?q=zzz");
  expect(await screen.findByText("Nenhuma licitação com esses filtros.")).toBeInTheDocument();
  expect(within(screen.getByTestId("vazio-lista")).getByRole("button", { name: "Limpar filtros" })).toBeInTheDocument();
});

test("titulo da linha e link com tituloObjeto", async () => {
  padrao();
  const tela = montar();
  const links = await within(tela).findAllByRole("link", { name: /Monitoramento de débitos|Participação de 03/i });
  expect(links.length).toBeGreaterThan(0);
  const primeiro = links[0] as HTMLAnchorElement;
  expect(primeiro.getAttribute("href")).toMatch(/^\/licitacao\/\d+/);
  expect(within(primeiro).getByRole("heading")).toBeInTheDocument();
});

test("filtro de modalidade vem da faceta e vai para a URL", async () => {
  padrao();
  let ultima = "";
  const comModalidade = { ...licitacoes, facetas: { ...licitacoes.facetas, modalidade: { "Pregão eletrônico": 40, "Concorrência": 3 } } };
  servidor.use(http.get("/api/v2/licitacoes", ({ request }) => {
    ultima = new URL(request.url).search;
    return HttpResponse.json(comModalidade);
  }));
  const tela = montar();
  const opcao = await within(tela).findByRole("radio", { name: /Concorrência/ });
  await userEvent.setup().click(opcao);
  await vi.waitFor(() => expect(decodeURIComponent(ultima)).toContain("modalidade=Concorrência"));
});

test("modalidade vinda da URL aparece marcada e pode ser removida", async () => {
  padrao();
  let ultima = "";
  const comModalidade = { ...licitacoes, facetas: { ...licitacoes.facetas, modalidade: { "Pregão eletrônico": 40 } } };
  servidor.use(http.get("/api/v2/licitacoes", ({ request }) => {
    ultima = new URL(request.url).search;
    return HttpResponse.json(comModalidade);
  }));
  const tela = montar("/?modalidade=Leil%C3%A3o");
  const marcada = await within(tela).findByRole("radio", { name: /Leilão/ });
  expect(marcada).toBeChecked();
  await userEvent.setup().click(within(tela).getByRole("radio", { name: /Todas/ }));
  await vi.waitFor(() => expect(ultima).not.toContain("modalidade"));
});
